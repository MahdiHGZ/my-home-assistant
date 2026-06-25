"""Yeelight bulb utilities — discovery, control, and state management."""

from __future__ import annotations

import colorsys
import json
import logging
import os
import random
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from yeelight import Bulb, discover_bulbs

from tooling import brain_tool

logger = logging.getLogger(__name__)

_BULB_CONFIG = Path("bulb.json")
_STATE_HISTORY_SIZE = 20
_state_stack: deque[dict[str, dict]] = deque(maxlen=_STATE_HISTORY_SIZE)

# Shared worker pool — creating a ThreadPoolExecutor per call is pure
# overhead in the hot path (the party dance ticks twice a second).
_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="bulb")

# Connection cache: yeelight keeps a persistent TCP socket per Bulb object,
# so reusing instances avoids a handshake (and three for multi-step modes)
# on every command. Entries are dropped on error so the next call reconnects.
_bulb_cache: dict[str, Bulb] = {}
_bulb_cache_lock = threading.Lock()


def _get_bulb(ip: str) -> Bulb:
    with _bulb_cache_lock:
        bulb = _bulb_cache.get(ip)
        if bulb is None:
            bulb = Bulb(ip)
            _bulb_cache[ip] = bulb
        return bulb


def _invalidate_bulb(ip: str) -> None:
    with _bulb_cache_lock:
        _bulb_cache.pop(ip, None)


def _invalidate_all_bulbs() -> None:
    with _bulb_cache_lock:
        _bulb_cache.clear()


# ---------------------------------------------------------------------------
# Mode definitions (single source of truth for all presets)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BulbMode:
    """Declarative bulb preset. Fields set to None are left unchanged."""

    name: str
    color: tuple[int, int, int] | None = None  # RGB
    color_temp: int | None = None               # Kelvin
    brightness: int | None = None               # 1–100


COLOR_CYCLE: list[str] = [
    "red",
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
]

MODES: dict[str, BulbMode] = {
    "full_on":    BulbMode("full on",        color_temp=6500, brightness=100),
    "max_bright": BulbMode("max brightness", brightness=100),
    "cool_white": BulbMode("cool white",    color_temp=6500, brightness=100),
    "warm_white": BulbMode("warm white",    color_temp=3000, brightness=100),
    "sleep":      BulbMode("sleep",          color=(35, 78, 134),  brightness=20),
    "romantic":   BulbMode("romantic",       color=(255, 50, 80),  brightness=10),
    "movie":      BulbMode("movie",          color=(10, 20, 60),   brightness=3),
    "sunset":     BulbMode("sunset",         color=(255, 100, 50), brightness=40),

    "red": BulbMode("red", color=(255, 0, 0)),
    "orange": BulbMode("orange", color=(255, 165, 0)),
    "yellow": BulbMode("yellow", color=(255, 255, 0)),
    "green": BulbMode("green", color=(0, 255, 0)),
    "cyan": BulbMode("cyan", color=(0, 255, 255)),
    "blue": BulbMode("blue", color=(0, 0, 255)),
    "purple": BulbMode("purple", color=(128, 0, 255)),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wait_all(futures: list[Future]) -> None:
    """Waits for all futures; per-bulb errors are handled inside the actions."""
    for future in futures:
        future.result()


def _for_each_bulb(
    registered: dict[str, str],
    action: Callable[[str, str], None],
) -> None:
    """Runs action(name, ip) on every registered bulb concurrently."""
    if not registered:
        logger.warning("No bulbs are registered.")
        return
    _wait_all([_EXECUTOR.submit(action, name, ip) for name, ip in registered.items()])


def _random_vivid_rgb() -> tuple[int, int, int]:
    """Generates a vivid random RGB color using full-saturation HSV."""
    h = random.random()
    r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


# ---------------------------------------------------------------------------
# Count sequences (3x2 layout)
# ---------------------------------------------------------------------------

_COUNT_STEP_DELAY_S = 0.8
_COUNT_ROWS = ("I1", "I2", "I3")
_COUNT_COLS = ("I4", "I5", "I6")
_COUNT_COLORS = ("red", "yellow", "green", "blue")
_COUNT_ROW_PAIRS = (("I1", "I4"), ("I2", "I5"), ("I3", "I6"))


def _for_each_named_bulb(
    registered: dict[str, str],
    names: Iterable[str],
    action: Callable[[str, str], None],
) -> None:
    """Runs action(name, ip) concurrently on a named subset of bulbs."""
    names_list = list(names)
    if not names_list:
        return

    futures = []
    for name in names_list:
        ip = registered.get(name)
        if not ip:
            logger.warning("Missing bulb %s in config.", name)
            continue
        futures.append(_EXECUTOR.submit(action, name, ip))
    _wait_all(futures)


def _apply_mode_to_subset(
    registered: dict[str, str],
    names: Iterable[str],
    mode: BulbMode | str,
) -> None:
    """Applies a mode to only the requested bulb names."""
    resolved_mode = _resolve_mode(mode)

    def _apply(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            _apply_mode_to_bulb(bulb, resolved_mode)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_named_bulb(registered, names, _apply)


def _turn_off_subset(registered: dict[str, str], names: Iterable[str]) -> None:
    """Turns off only the requested bulb names."""

    def _turn_off(name: str, ip: str) -> None:
        try:
            _get_bulb(ip).turn_off()
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_named_bulb(registered, names, _turn_off)


def run_row_pair_sweep_count(registered: dict[str, str] | None = None) -> None:
    """Lights row pairs in sequence: I1/I4, I2/I5, then I3/I6."""
    reg = registered if registered is not None else load_registered_bulbs()
    full_off(reg)
    for pair in _COUNT_ROW_PAIRS:
        time.sleep(_COUNT_STEP_DELAY_S)
        _apply_mode_to_subset(reg, pair, MODES["full_on"])


def run_column_count_with_row_carry(registered: dict[str, str] | None = None) -> None:
    """Counts colors on columns and carries progress onto rows."""
    reg = registered if registered is not None else load_registered_bulbs()
    full_off(reg)

    for step_index, color_name in enumerate(_COUNT_COLORS):
        for column_bulb in _COUNT_COLS:
            time.sleep(_COUNT_STEP_DELAY_S)
            _apply_mode_to_subset(reg, (column_bulb,), MODES[color_name])

        carried_rows = _COUNT_ROWS[: step_index + 1]
        _apply_mode_to_subset(reg, carried_rows, MODES[color_name])
        if step_index < len(_COUNT_COLORS) - 1:
            _turn_off_subset(reg, _COUNT_COLS)


# ---------------------------------------------------------------------------
# State save / restore (undo)
# ---------------------------------------------------------------------------

def read_state(registered: dict[str, str]) -> dict[str, dict]:
    """Reads the current state of all bulbs concurrently.

    Returns:
        Dict mapping bulb names to their property dicts.
    """
    state: dict[str, dict] = {}

    def _read(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            props = bulb.get_properties()
            state[name] = {
                "ip": ip,
                "power": props.get("power", "off"),
                "bright": int(props.get("bright", 100)),
                "color_mode": int(props.get("color_mode", 2)),
                "ct": int(props.get("ct", 6500)),
                "rgb": int(props.get("rgb", 0)),
            }
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): failed to read state — %s", name, ip, e)

    _for_each_bulb(registered, _read)
    return state


def save_state(registered: dict[str, str]) -> None:
    """Reads current bulb state and pushes it onto the undo stack."""
    state = read_state(registered)
    _state_stack.append(state)
    logger.debug("State saved (%d in history).", len(_state_stack))


def apply_state(registered: dict[str, str], state: dict[str, dict]) -> None:
    """Applies a previously captured state to all bulbs.

    Args:
        registered: Bulb name-to-IP mapping.
        state: State dict as returned by read_state().
    """
    logger.info("Applying saved state.")

    def _restore(name: str, ip: str) -> None:
        if name not in state:
            return
        s = state[name]
        try:
            bulb = _get_bulb(ip)
            if s["power"] == "off":
                bulb.turn_off()
                logger.info("  %s (%s): → OFF", name, ip)
                return
            bulb.turn_on()
            if s["color_mode"] == 1:  # RGB
                rgb_val = s["rgb"]
                r = (rgb_val >> 16) & 0xFF
                g = (rgb_val >> 8) & 0xFF
                b = rgb_val & 0xFF
                bulb.set_rgb(r, g, b)
            else:  # color temperature (mode 2) or HSV (mode 3)
                bulb.set_color_temp(s["ct"])
            bulb.set_brightness(s["bright"])
            _persist_default(bulb)
            logger.info("  %s (%s): restored", name, ip)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _restore)


def undo_depth() -> int:
    """Returns how many saved states remain on the undo stack."""
    return len(_state_stack)


def restore_state(registered: dict[str, str]) -> bool:
    """Pops the last saved state from the undo stack and applies it.

    Returns:
        True if a state was restored, False if the stack was empty.
    """
    if not _state_stack:
        logger.warning("No saved state to restore.")
        return False
    state = _state_stack.pop()
    logger.info("Undo — restoring previous state (%d remaining).", len(_state_stack))
    apply_state(registered, state)
    return True


# ---------------------------------------------------------------------------
# Party dance (background color patterns)
# ---------------------------------------------------------------------------

# Bulb grid layout:
#   I1 I4
#   I2 I5
#   I3 I6

@dataclass(frozen=True)
class DancePattern:
    """Dance pattern definition. None positions = random each tick."""

    name: str
    positions: dict[str, int] | None


DANCE_PATTERNS: list[DancePattern] = [
    DancePattern("snake wave", {
        "I1": 0, "I2": 1, "I3": 2,
        "I6": 3, "I5": 4, "I4": 5,
    }),
    DancePattern("pulse", {
        "I1": 0, "I2": 0, "I3": 0,
        "I4": 0, "I5": 0, "I6": 0,
    }),
    DancePattern("row sweep", {
        "I1": 0, "I4": 0,
        "I2": 2, "I5": 2,
        "I3": 4, "I6": 4,
    }),
    DancePattern("waterfall", {
        "I1": 0, "I2": 1, "I3": 2,
        "I4": 1, "I5": 2, "I6": 3,
    }),
    DancePattern("cross", {
        "I1": 0, "I6": 0,
        "I2": 2, "I5": 2,
        "I3": 4, "I4": 4,
    }),
    DancePattern("random", None),
]

_party_thread: threading.Thread | None = None
_party_stop = threading.Event()
_party_pattern_index = 0
_music_ips: set[str] = set()

_PARTY_INTERVAL_S = 0.5

# How often the dance re-establishes music mode. A music-mode connection can
# drop silently — the bulb then reverts to its warm power-on default while our
# fire-and-forget set_rgb calls keep "succeeding" into a dead socket (the
# library returns ok without reading back). Periodically tearing music mode
# down and rebuilding it re-lights any bulb that fell back; visually it is
# indistinguishable from a normal colour step.
_PARTY_REFRESH_S = 30.0


def get_party_pattern() -> DancePattern:
    """Returns the currently active dance pattern."""
    return DANCE_PATTERNS[_party_pattern_index]


def set_party_pattern(index: int) -> DancePattern:
    """Selects a dance pattern by index (wraps around) and returns it.

    The dance loop reads the index each tick, so this applies live while the
    party is running.
    """
    global _party_pattern_index
    _party_pattern_index = int(index) % len(DANCE_PATTERNS)
    p = DANCE_PATTERNS[_party_pattern_index]
    logger.info(
        "Dance pattern set: %s (%d/%d).",
        p.name, _party_pattern_index + 1, len(DANCE_PATTERNS),
    )
    return p


def next_party_pattern() -> DancePattern:
    """Advances to the next dance pattern and returns it."""
    global _party_pattern_index
    _party_pattern_index = (_party_pattern_index + 1) % len(DANCE_PATTERNS)
    p = DANCE_PATTERNS[_party_pattern_index]
    logger.info(
        "Dance pattern → %s (%d/%d).",
        p.name, _party_pattern_index + 1, len(DANCE_PATTERNS),
    )
    return p


def prev_party_pattern() -> DancePattern:
    """Goes back to the previous dance pattern and returns it."""
    global _party_pattern_index
    _party_pattern_index = (_party_pattern_index - 1) % len(DANCE_PATTERNS)
    p = DANCE_PATTERNS[_party_pattern_index]
    logger.info(
        "Dance pattern → %s (%d/%d).",
        p.name, _party_pattern_index + 1, len(DANCE_PATTERNS),
    )
    return p


def start_party_dance(registered: dict[str, str]) -> None:
    """Starts a background thread that cycles colors across bulbs
    using the currently selected dance pattern."""
    global _party_thread
    stop_party_dance()
    _party_stop.clear()

    def _init_bulb(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            bulb.turn_on()
            bulb.set_brightness(100)
        except Exception:
            _invalidate_bulb(ip)
            return
        # Music mode removes the ~60 commands/min LAN rate limit, which the
        # dance loop would otherwise exceed. Best-effort: without it the
        # dance still runs over normal (rate-limited) commands.
        try:
            bulb.start_music()
            _music_ips.add(ip)
        except Exception as e:
            logger.debug("  %s (%s): music mode unavailable — %s", name, ip, e)

    def _reinit_bulb(name: str, ip: str) -> None:
        # Drop any (possibly half-open) music connection and re-establish it
        # from scratch, so a silently dropped socket can't strand the bulb at
        # its warm power-on default for the rest of the dance.
        if ip in _music_ips:
            try:
                _get_bulb(ip).stop_music()
            except Exception:
                pass
            _music_ips.discard(ip)
        _invalidate_bulb(ip)
        _init_bulb(name, ip)

    def _dance() -> None:
        p = DANCE_PATTERNS[_party_pattern_index]
        logger.info("Party dance started — %s.", p.name)
        _for_each_bulb(registered, _init_bulb)
        refresh_ticks = max(1, round(_PARTY_REFRESH_S / _PARTY_INTERVAL_S))
        tick = 0
        n_colors = len(COLOR_CYCLE)
        while not _party_stop.is_set():
            if tick and tick % refresh_ticks == 0:
                _for_each_bulb(registered, _reinit_bulb)
            current_tick = tick
            tick += 1
            pattern = DANCE_PATTERNS[_party_pattern_index]

            def _set_color(name: str, ip: str) -> None:
                try:
                    if pattern.positions is None:
                        color = random.choice(COLOR_CYCLE)
                    else:
                        pos = pattern.positions.get(name, 0)
                        color = COLOR_CYCLE[(current_tick + pos) % n_colors]
                    bulb = _get_bulb(ip)
                    bulb.set_rgb(*MODES[color].color)
                except Exception:
                    _invalidate_bulb(ip)

            _for_each_bulb(registered, _set_color)
            _party_stop.wait(_PARTY_INTERVAL_S)
        logger.info("Party dance stopped.")

    _party_thread = threading.Thread(target=_dance, daemon=True)
    _party_thread.start()


def stop_party_dance() -> None:
    """Stops the party dance background thread if running.

    Always leaves music mode, even when the dance thread has already died —
    otherwise bulbs stay stranded in the volatile music state and revert to
    their warm power-on default when the connection later drops.
    """
    global _party_thread
    if _party_thread is not None and _party_thread.is_alive():
        _party_stop.set()
        _party_thread.join(timeout=5)
    _party_thread = None
    _exit_music_mode()


def _exit_music_mode() -> None:
    """Leaves music mode on all bulbs that entered it for the dance.

    The bulbs are then dropped from the connection cache so subsequent
    reads (e.g. save_state) use a fresh, normal connection.
    """
    for ip in list(_music_ips):
        try:
            _get_bulb(ip).stop_music()
        except Exception as e:
            logger.debug("Could not stop music mode for %s: %s", ip, e)
        _invalidate_bulb(ip)
        _music_ips.discard(ip)


def is_party_running() -> bool:
    """Returns True if the party dance loop is active."""
    return _party_thread is not None and _party_thread.is_alive()


# ---------------------------------------------------------------------------
# Config & discovery
# ---------------------------------------------------------------------------

# (mtime, bulbs) for the default config path — avoids re-parsing bulb.json
# on every keypress. Invalidated automatically when the file changes.
_config_cache: tuple[float, dict[str, str]] | None = None


def load_registered_bulbs(config_path: Path | None = None) -> dict[str, str]:
    """Loads bulb name-to-IP mappings from the JSON config file.

    Args:
        config_path: Override path to the bulb config. Defaults to bulb.json.

    Returns:
        Dict mapping bulb names to IP addresses.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        json.JSONDecodeError: If the config file is malformed.
    """
    global _config_cache
    path = config_path or _BULB_CONFIG
    use_cache = config_path is None

    if use_cache:
        mtime = os.path.getmtime(path)
        if _config_cache is not None and _config_cache[0] == mtime:
            return dict(_config_cache[1])

    with open(path) as f:
        data = json.load(f)
    bulbs = {name: info["ip"] for name, info in data.items()}
    logger.debug("Loaded %d bulb(s) from %s.", len(bulbs), path)

    if use_cache:
        _config_cache = (os.path.getmtime(path), dict(bulbs))
    return bulbs


def discover() -> set[str]:
    """Discovers Yeelight bulbs on the local network.

    Returns:
        Set of discovered bulb IP addresses.
    """
    logger.info("Searching for Yeelight bulbs on the network.")
    discovered = discover_bulbs()
    if not discovered:
        logger.warning(
            "No bulbs found. Ensure LAN control is enabled in the Yeelight app."
        )
        return set()
    ips = {b["ip"] for b in discovered}
    logger.info("Found %d bulb(s).", len(ips))
    return ips


def check_presence(
    registered: dict[str, str],
    discovered: set[str],
) -> dict[str, bool]:
    """Checks which registered bulbs are currently online.

    Args:
        registered: Bulb name-to-IP mapping.
        discovered: Set of discovered IP addresses.

    Returns:
        Dict mapping bulb names to their online status.
    """
    results = {}
    for name, ip in registered.items():
        online = ip in discovered
        results[name] = online
        status = "online" if online else "MISSING"
        logger.info("  %s (%s): %s", name, ip, status)

    if all(results.values()):
        logger.info("All registered bulbs are online.")
    else:
        logger.warning("Some registered bulbs are not reachable.")
    return results


# ---------------------------------------------------------------------------
# Bulb actions
# ---------------------------------------------------------------------------

def _resolve_mode(mode: BulbMode | str) -> BulbMode:
    """Resolve a mode object or mode key into a BulbMode."""
    if isinstance(mode, BulbMode):
        return mode

    mode_key = mode.strip().lower()
    if mode_key in MODES:
        return MODES[mode_key]

    available = ", ".join(sorted(MODES.keys()))
    raise KeyError(f"Unknown mode '{mode}'. Available modes: {available}")


def _persist_default(bulb: Bulb) -> None:
    """Saves the bulb's current state as its power-on default.

    Yeelight bulbs revert to a warm power-on default (warm yellow ~2700K)
    whenever they lose the state held in volatile memory — a brief power blip,
    a dropped LAN connection, or leaving a transient mode (music/flow). Writing
    the current state to flash with ``set_default`` means any such revert
    restores the *chosen* colour instead of the factory warm-white.

    Best-effort: bulbs that have the Yeelight app's "auto save settings"
    enabled already persist on change and raise a harmless error here, which we
    intentionally swallow.
    """
    try:
        bulb.set_default()
    except Exception as e:
        logger.debug("set_default (persist) failed — auto-save may be on: %s", e)


def _apply_mode_to_bulb(bulb: Bulb, mode: BulbMode) -> None:
    """Apply one BulbMode to one bulb."""
    bulb.turn_on()
    if mode.color is not None:
        bulb.set_rgb(*mode.color)
    elif mode.color_temp is not None:
        bulb.set_color_temp(mode.color_temp)
    if mode.brightness is not None:
        bulb.set_brightness(mode.brightness)


def apply_mode(
    registered: dict[str, str],
    mode: BulbMode | str,
    *,
    persist: bool = True,
) -> None:
    """Applies a BulbMode preset to all registered bulbs.

    Turns on each bulb, then sets only the fields that are not None.
    This is the single entry point for all preset/mode changes.

    Args:
        registered: Bulb name-to-IP mapping.
        mode: The preset (BulbMode) or mode key from MODES.
        persist: When True (the default), save the resulting state as each
            bulb's power-on default so it survives a power blip or connection
            drop. Pass False for transient states the user did not choose to
            keep (e.g. the brief full-on flash before a photo capture).
    """
    resolved_mode = _resolve_mode(mode)
    logger.info("Applying mode: %s.", resolved_mode.name)

    def _apply(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            _apply_mode_to_bulb(bulb, resolved_mode)
            if persist:
                _persist_default(bulb)
            logger.info("  %s (%s): %s", name, ip, resolved_mode.name)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _apply)


def full_off(registered: dict[str, str]) -> None:
    """Turns all registered bulbs OFF and saves OFF as each bulb's power-on default.

    Persisting after ``turn_off`` prevents a later power or LAN glitch from
    restoring a previous ON scene that was saved by an earlier ``apply_mode``.

    Args:
        registered: Bulb name-to-IP mapping.
    """
    logger.info("Turning all bulbs OFF.")

    def _turn_off(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            bulb.turn_off()
            _persist_default(bulb)
            logger.info("  %s (%s): OFF", name, ip)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _turn_off)


def _parse_color_input(color: str) -> tuple[str, tuple[int, int, int] | int]:
    """Parse a user-provided color value into RGB or color temperature."""
    token = color.strip().lower()

    if token in MODES:
        mode = MODES[token]
        if mode.color is not None:
            return "rgb", mode.color
        if mode.color_temp is not None:
            return "ct", mode.color_temp

    kelvin_aliases = {
        "white": 6500,
        "cool": 6500,
        "cool_white": 6500,
        "cool white": 6500,
        "warm": 3000,
        "warm_white": 3000,
        "warm white": 3000,
    }
    if token in kelvin_aliases:
        return "ct", kelvin_aliases[token]

    if token.startswith("#") and len(token) == 7:
        try:
            r = int(token[1:3], 16)
            g = int(token[3:5], 16)
            b = int(token[5:7], 16)
            return "rgb", (r, g, b)
        except ValueError as exc:
            raise ValueError(f"Invalid hex color: {color}") from exc

    if "," in token:
        parts = [p.strip() for p in token.split(",")]
        if len(parts) == 3:
            try:
                r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError as exc:
                raise ValueError(f"Invalid RGB color: {color}") from exc
            if not all(0 <= x <= 255 for x in (r, g, b)):
                raise ValueError("RGB values must be between 0 and 255.")
            return "rgb", (r, g, b)

    # Plain digits in Yeelight CT range (Kelvin), e.g. "3000", "6500"
    if token.isdigit():
        kelvin = int(token)
        if 1700 <= kelvin <= 6500:
            return "ct", kelvin

    raise ValueError(
        "Unsupported color. Use a mode name (red/blue/warm_white), "
        "Kelvin as digits (1700-6500), hex (#RRGGBB), or RGB string (r,g,b)."
    )


def _resolve_registered_light_name(registered: dict[str, str], light_name: str) -> str:
    """Resolve user-provided light name against config keys (case-insensitive)."""
    candidate = light_name.strip()
    if not candidate:
        raise ValueError("light_name cannot be empty.")

    if candidate in registered:
        return candidate

    lookup = {name.strip().lower(): name for name in registered}
    key = candidate.lower()
    if key in lookup:
        return lookup[key]

    upper_key = candidate.upper()
    if upper_key in registered:
        return upper_key

    available = ", ".join(sorted(registered.keys()))
    raise KeyError(f"Unknown light '{light_name}'. Available names: {available}")


def _current_fields_from_props(props: dict[str, str] | None) -> dict[str, object]:
    """Map Yeelight get_properties() output to stable ``current_*`` return keys."""
    if not props:
        return {
            "current_power": None,
            "current_brightness": None,
            "current_color_mode": None,
            "current_rgb": None,
            "current_color_temp": None,
        }
    return {
        "current_power": props.get("power"),
        "current_brightness": props.get("bright"),
        "current_color_mode": props.get("color_mode"),
        "current_rgb": props.get("rgb"),
        "current_color_temp": props.get("ct"),
    }


def _resolve_bulb_targets(
    registered: dict[str, str],
    bulbs: str | Iterable[str] | None,
) -> list[tuple[str, str]]:
    """Resolve selection into sorted unique ``(name, ip)`` pairs."""
    if not registered:
        return []
    if bulbs is None:
        names = sorted(registered.keys())
        return [(n, registered[n]) for n in names]
    if isinstance(bulbs, str):
        token = bulbs.strip()
        if token.lower() in ("", "all", "*"):
            names = sorted(registered.keys())
            return [(n, registered[n]) for n in names]
        raw = [piece.strip() for piece in token.split(",") if piece.strip()]
    else:
        raw = [str(x).strip() for x in bulbs if str(x).strip()]
    if not raw:
        raise ValueError("No valid light names were provided.")
    seen: set[str] = set()
    ordered: list[str] = []
    for piece in raw:
        name = _resolve_registered_light_name(registered, piece)
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    ordered.sort()
    return [(n, registered[n]) for n in ordered]


def _read_bulb_entry(name: str, ip: str) -> dict[str, object]:
    """Fetch live state for one bulb (list mode)."""
    try:
        props = _get_bulb(ip).get_properties()
        return {
            "ok": True,
            "name": name,
            "ip": ip,
            "actions": [],
            **_current_fields_from_props(props),
        }
    except Exception as e:
        _invalidate_bulb(ip)
        logger.error("  %s (%s): failed to read state — %s", name, ip, e)
        return {
            "ok": False,
            "name": name,
            "ip": ip,
            "error": str(e),
            "actions": [],
            **_current_fields_from_props(None),
        }


def _apply_direct_controls_to_bulb(
    bulb: Bulb,
    *,
    power: bool | None,
    brightness: int | None,
    parsed_color: tuple[str, tuple[int, int, int] | int] | None,
) -> list[str]:
    """Apply direct user controls and return the action summary."""
    actions: list[str] = []

    if power is not None:
        if power:
            bulb.turn_on()
            actions.append("power:on")
        else:
            bulb.turn_off()
            actions.append("power:off")

    if parsed_color is not None:
        bulb.turn_on()
        color_type, color_value = parsed_color
        if color_type == "rgb":
            r, g, b = color_value
            bulb.set_rgb(r, g, b)
            actions.append(f"color:rgb({r},{g},{b})")
        else:
            kelvin = int(color_value)
            bulb.set_color_temp(kelvin)
            actions.append(f"color_temp:{kelvin}K")

    if brightness is not None:
        bulb.turn_on()
        level = max(1, min(100, int(brightness)))
        bulb.set_brightness(level)
        actions.append(f"brightness:{level}")

    return actions


def _control_bulb_apply(
    name: str,
    ip: str,
    *,
    power: bool | None,
    brightness: int | None,
    parsed_color: tuple[str, tuple[int, int, int] | int] | None,
) -> dict[str, object]:
    """Apply direct controls to one bulb; return one ``bulbs[]`` entry."""
    try:
        bulb = _get_bulb(ip)
        actions = _apply_direct_controls_to_bulb(
            bulb,
            power=power,
            brightness=brightness,
            parsed_color=parsed_color,
        )
        # Persist only when a colour was chosen. The web colour picker / temp
        # slider fire on `change` (one final value), so this stays a single
        # command per pick; the brightness slider streams `input` events and is
        # deliberately left unpersisted to stay within the LAN rate limit.
        if parsed_color is not None:
            _persist_default(bulb)
        if actions:
            logger.info("Bulb %s (%s): %s", name, ip, ", ".join(actions))
        props = bulb.get_properties()
        return {
            "ok": True,
            "name": name,
            "ip": ip,
            "actions": actions,
            **_current_fields_from_props(props),
        }
    except Exception as e:
        _invalidate_bulb(ip)
        logger.error("  %s (%s): FAILED — %s", name, ip, e)
        return {
            "ok": False,
            "name": name,
            "ip": ip,
            "error": str(e),
            "actions": [],
            **_current_fields_from_props(None),
        }


@brain_tool
def control_bulbs(
    bulbs: str | Iterable[str] | None = None,
    *,
    power: bool | None = None,
    brightness: int | None = None,
    color: str | None = None,
    registered: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> dict[str, object]:
    """Primary API: list bulb state from the LAN, or set power, brightness, and color.

    **List mode** — omit ``power``, ``brightness``, and ``color``. Reads each target
    bulb's live properties. ``bulbs=None`` selects every entry in ``bulb.json``.

    **Control mode** — pass any of those three. Same target rules; work runs in parallel.

    Target strings accept comma-separated names, or ``all`` / ``*`` for every registered bulb.

    Args:
        bulbs: ``None`` = all; ``str`` = one name, comma-separated names, or ``all``/``*``;
            iterable = explicit subset (resolved case-insensitively).
        power: ``True`` on, ``False`` off, ``None`` leave unchanged.
        brightness: 1-100, or ``None``.
        color: Mode key, Kelvin digits (1700-6500), ``#RRGGBB``, ``r,g,b``, or warm/cool aliases.
        registered: Optional cached name-to-IP map; loads from config if omitted.
        config_path: Optional path passed to :func:`load_registered_bulbs`.

    Returns:
        Dict with:

        - ``ok``: every targeted bulb operation succeeded.
        - ``mode``: ``\"list\"`` or ``\"control\"``.
        - ``count``: length of ``bulbs``.
        - ``registered_names``: sorted keys from config (discovery helper).
        - ``bulbs``: list of dicts, each with ``name``, ``ip``, ``ok``, optional ``error``,
          ``actions`` (empty in list mode when idle), and ``current_*`` Yeelight fields.
    """
    reg = registered if registered is not None else load_registered_bulbs(config_path)
    registered_names = sorted(reg.keys())
    targets = _resolve_bulb_targets(reg, bulbs)

    base: dict[str, object] = {
        "registered_names": registered_names,
        "count": len(targets),
        "bulbs": [],
    }

    if power is None and brightness is None and color is None:
        if not targets:
            return {"ok": True, "mode": "list", **base, "bulbs": []}
        futures = [_EXECUTOR.submit(_read_bulb_entry, n, ip) for n, ip in targets]
        entries = [f.result() for f in futures]
        entries.sort(key=lambda e: str(e["name"]))
        all_ok = all(bool(e.get("ok")) for e in entries)
        return {"ok": all_ok, "mode": "list", **base, "bulbs": entries, "count": len(entries)}

    parsed_color: tuple[str, tuple[int, int, int] | int] | None = None
    if color is not None:
        parsed_color = _parse_color_input(color)

    if not targets:
        return {"ok": True, "mode": "control", **base, "bulbs": []}

    futures = [
        _EXECUTOR.submit(
            _control_bulb_apply,
            n,
            ip,
            power=power,
            brightness=brightness,
            parsed_color=parsed_color,
        )
        for n, ip in targets
    ]
    entries = [f.result() for f in futures]
    entries.sort(key=lambda e: str(e["name"]))
    all_ok = all(bool(e.get("ok")) for e in entries)
    return {"ok": all_ok, "mode": "control", **base, "bulbs": entries, "count": len(entries)}


@brain_tool
def control_light(
    light_name: str,
    power: bool | None = None,
    brightness: int | None = None,
    color: str | None = None,
) -> dict[str, object]:
    """Single-bulb wrapper around :func:`control_bulbs` (legacy return shape for tools)."""
    result = control_bulbs(
        bulbs=light_name,
        power=power,
        brightness=brightness,
        color=color,
    )
    items = result.get("bulbs")
    if not isinstance(items, list) or len(items) != 1:
        raise RuntimeError(f"No result for light '{light_name}'.")
    entry = items[0]
    if not entry.get("ok"):
        raise RuntimeError(str(entry.get("error", "Unknown light control error.")))
    legacy: dict[str, object] = {
        "ok": True,
        "light_name": entry["name"],
        "ip": entry["ip"],
        "current_power": entry.get("current_power"),
        "current_brightness": entry.get("current_brightness"),
        "current_color_mode": entry.get("current_color_mode"),
        "current_rgb": entry.get("current_rgb"),
        "current_color_temp": entry.get("current_color_temp"),
    }
    actions = entry.get("actions")
    if isinstance(actions, list) and actions:
        legacy["actions"] = actions
    else:
        legacy["message"] = "No change requested."
    return legacy


def adjust_brightness(registered: dict[str, str], step: int) -> None:
    """Adjusts brightness of all registered bulbs by a relative step.

    When decreasing below the step size, the bulb is turned off.
    When increasing while off, the bulb is turned on at the step value.

    Args:
        registered: Bulb name-to-IP mapping.
        step: Brightness delta (-100 to 100). Positive = brighter.
    """
    direction = "up" if step > 0 else "down"
    logger.info("Adjusting brightness %s by %d%%.", direction, abs(step))

    def _adjust(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            props = bulb.get_properties()
            is_off = props.get("power") == "off"

            if is_off and step <= 0:
                logger.debug("  %s (%s): skipped (already off)", name, ip)
                return

            if is_off and step > 0:
                bulb.turn_on()
                bulb.set_brightness(step)
                _persist_default(bulb)
                logger.info("  %s (%s): OFF → %d%%", name, ip, step)
                return

            current = int(props.get("bright", 50))
            target = current + step

            if target <= 0:
                bulb.turn_off()
                logger.info("  %s (%s): %d%% → OFF", name, ip, current)
            else:
                target = min(100, target)
                bulb.set_brightness(target)
                _persist_default(bulb)
                logger.info("  %s (%s): %d%% → %d%%", name, ip, current, target)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _adjust)


def set_random_color(registered: dict[str, str]) -> None:
    """Sets all bulbs to the same random vivid color.

    Args:
        registered: Bulb name-to-IP mapping.
    """
    r, g, b = _random_vivid_rgb()
    logger.info("Setting all bulbs to RGB(%d, %d, %d).", r, g, b)

    def _set_color(name: str, ip: str) -> None:
        try:
            bulb = _get_bulb(ip)
            bulb.turn_on()
            bulb.set_rgb(r, g, b)
            _persist_default(bulb)
            logger.info("  %s (%s): RGB(%d, %d, %d)", name, ip, r, g, b)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _set_color)


def set_random_color_each(registered: dict[str, str]) -> None:
    """Sets each bulb to a different random vivid color.

    Args:
        registered: Bulb name-to-IP mapping.
    """
    logger.info("Setting each bulb to a different random color.")

    def _set_color(name: str, ip: str) -> None:
        try:
            r, g, b = _random_vivid_rgb()
            bulb = _get_bulb(ip)
            bulb.turn_on()
            bulb.set_rgb(r, g, b)
            _persist_default(bulb)
            logger.info("  %s (%s): RGB(%d, %d, %d)", name, ip, r, g, b)
        except Exception as e:
            _invalidate_bulb(ip)
            logger.error("  %s (%s): FAILED — %s", name, ip, e)

    _for_each_bulb(registered, _set_color)


_DISCOVERY_SWEEPS = 3


def update_discover_config() -> None:
    """Rediscovers bulb IPs and updates bulb.json.

    Bounded to a few discovery sweeps — bulbs that stay offline keep their
    previous IP instead of hanging the caller. The config file is written
    atomically so a crash mid-write cannot corrupt it.
    """
    logger.info("[discovery] Starting discovery")
    with open(_BULB_CONFIG) as f:
        config = json.load(f)

    remaining = {c["id"] for c in config.values() if "id" in c}
    for sweep in range(_DISCOVERY_SWEEPS):
        if not remaining:
            break
        logger.info(
            "[discovery] sweep %d/%d — %d bulb(s) remaining...",
            sweep + 1, _DISCOVERY_SWEEPS, len(remaining),
        )
        for found in discover_bulbs(timeout=3):
            found_id = found["capabilities"]["id"]
            if found_id not in remaining:
                continue
            remaining.discard(found_id)
            for entry in config.values():
                if entry.get("id") == found_id:
                    entry["ip"] = found["ip"]

    if remaining:
        logger.warning(
            "[discovery] %d bulb(s) not found; keeping their previous IPs.",
            len(remaining),
        )

    tmp_path = _BULB_CONFIG.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=4)
    os.replace(tmp_path, _BULB_CONFIG)

    # IPs may have changed — force fresh connections.
    _invalidate_all_bulbs()

# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    registered_ips = load_registered_bulbs()
    discovered_ips = discover()
    if not discovered_ips:
        raise SystemExit(1)
    check_presence(registered_ips, discovered_ips)
    apply_mode(registered_ips, MODES["full_on"])
