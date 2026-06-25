"""Coukab LAN — Home automation controller.

Single keys:
  KP1:        Vacuum: toggle — start sweep at full suction / stop & dock.
  KPPLUS:     Brightness up one step.
  KPMINUS:    Brightness down one step.
  KPASTERISK: Cool white (6500K).
  KPSLASH:    Warm white (3000K).
  KP4:        Party dance (toggle — random colors cycling).
  KP5:        Enter color cycle (starts at red).
  KP6:        Sunset mode (warm orange, 40%).
  KP7:        Sleep mode (deep blue, 20%).
  KP8:        Romantic mode (warm rose, 10%).
  KP9:        Movie mode (navy blue, 3%).

Combo keys (hold KP0 as modifier):
  KP0 + KP1:         Capture photo (no flash, current light).
  KP0 + KP2:         Capture photo with flash (blink off->on, then restore).
  KP0 + KP3:         Countdown (row-pair sweep), then capture with flash blink.
  KP0 + KP4:         Previous (color cycle / dance pattern).
  KP0 + KP6:         Next (color cycle / dance pattern).
  KP0 + KPPLUS:      Full brightness (100%) without changing color.
  KP0 + KPMINUS:     Turn off all bulbs.
  KP0 + KPASTERISK:  Random color (same for all bulbs).
  KP0 + KPSLASH:     Random color (different per bulb).
  KP0 + BACKSPACE:   Undo — restore previous bulb state.

Web interface:
  Running main.py also starts a no-login LAN web UI (default http://0.0.0.0:8080)
  so the same devices can be controlled from a phone or laptop — useful when the
  keypad is unavailable. The web UI adds richer controls than the keypad (per-bulb
  color, sliders, full vacuum/purifier controls, camera, optional LLM chat).
  Disable with --no-web; change the address with --web-host / --web-port.
  If the keypad device can't be opened, the web interface keeps running anyway.

Usage:
  python main.py --device /dev/input/event3
  python main.py --device /dev/input/event3 --grab -v
  python main.py --web-port 8080            # keypad + web (default)
  python main.py --no-web                   # keypad only
"""

from __future__ import annotations

import argparse
import functools
import logging
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import evdev

import tapo_camera_utils
import xiaomi_vacuum_utils
import yeelight_bulb_utils
from yeelight_bulb_utils import COLOR_CYCLE, MODES

logger = logging.getLogger(__name__)

_KEY_DOWN = 1
_KEY_UP = 0

_BRIGHTNESS_STEP = 20
_CAPTURE_SETTLE_S = 2
_FLASH_BLINK_OFF_S = 0.2

# ---------------------------------------------------------------------------
# Data-driven key → mode mappings.
# To add a new mode key: add one entry to MODES in yeelight_bulb_utils.py, then
# add one entry here. No new functions needed.
# ---------------------------------------------------------------------------

_SINGLE_KEY_MODES: dict[str, yeelight_bulb_utils.BulbMode] = {
    "KEY_KPASTERISK": MODES["cool_white"],
    "KEY_KPSLASH":    MODES["warm_white"],
    "KEY_KP6":        MODES["sunset"],
    "KEY_KP7":        MODES["sleep"],
    "KEY_KP8":        MODES["romantic"],
    "KEY_KP9":        MODES["movie"],
}

_COMBO_KEY_MODES: dict[str, yeelight_bulb_utils.BulbMode] = {
    "KEY_KPPLUS": MODES["max_bright"],
}


# ---------------------------------------------------------------------------
# Camera capture helpers
# ---------------------------------------------------------------------------

def _capture(bulbs: dict[str, str], *, flash: bool) -> None:
    """Captures a photo, optionally flashing full brightness first.

    The camera is connected before any lighting changes, so a slow or
    failed RTSP connect never leaves the room stuck in flash lighting.
    """
    cap = tapo_camera_utils.connect()
    if cap is None:
        logger.error("Camera connection failed.")
        return

    snapshot = None
    try:
        if flash:
            snapshot = yeelight_bulb_utils.read_state(bulbs)
            _prepare_flash_lighting(bulbs)
        tapo_camera_utils.capture_moment(cap)
    finally:
        cap.release()
        if snapshot is not None:
            yeelight_bulb_utils.apply_state(bulbs, snapshot)


def _prepare_flash_lighting(bulbs: dict[str, str]) -> None:
    """Creates a quick blink before full-on flash lighting."""
    yeelight_bulb_utils.full_off(bulbs)
    time.sleep(_FLASH_BLINK_OFF_S)
    # Transient capture lighting — don't save it as the bulbs' power-on
    # default; the real state is restored (and persisted) afterward.
    yeelight_bulb_utils.apply_mode(bulbs, MODES["full_on"], persist=False)
    time.sleep(_CAPTURE_SETTLE_S)


def _countdown_capture_with_flash(bulbs: dict[str, str]) -> None:
    """Runs countdown and camera connect in parallel, then captures with flash."""
    snapshot = yeelight_bulb_utils.read_state(bulbs)
    cap = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            countdown_future = pool.submit(yeelight_bulb_utils.run_row_pair_sweep_count, bulbs)
            cap = tapo_camera_utils.connect()
            countdown_future.result()

        if cap is None:
            logger.error("Camera connection failed.")
            return

        _prepare_flash_lighting(bulbs)
        tapo_camera_utils.capture_moment(cap)
    finally:
        if cap is not None:
            cap.release()
        yeelight_bulb_utils.apply_state(bulbs, snapshot)


def retry_with_discovery(max_attempts=3):
    """Retries bulb actions after rediscovering bulb IPs.

    After the final attempt the error is logged, not raised — a failed key
    action must never take down the event loop.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception:
                    logger.exception(
                        "%s failed (attempt %d/%d).", fn.__name__, attempt, max_attempts
                    )
                    if attempt == max_attempts:
                        break
                    yeelight_bulb_utils.update_discover_config()
                    time.sleep(1)
            logger.error("Giving up on %s after %d attempts.", fn.__name__, max_attempts)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------

class Controller:
    """Serialized action dispatcher shared by the keypad and the web UI.

    Jobs run on a single worker thread so a slow device operation
    (vacuum connect, RTSP, rediscovery) never blocks the evdev read loop,
    and so light actions — which share the party-dance thread and the
    color-cycle index — never race each other regardless of source.

    The ``_do_*`` methods hold the actual device logic and are reused by
    both the keypad handlers and :meth:`run_action` (the web entry point).
    """

    def __init__(self) -> None:
        self._color_cycle_index = 0
        # Last applied scene mode name (e.g. "movie"); None after all-off.
        # Surfaced via state() so remote panels can highlight the active mode.
        self._last_mode: str | None = None
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        # Called after every completed job (keypad or web). The web server
        # hooks this to push live status updates to connected browsers.
        self.on_action: Callable[[], None] | None = None
        threading.Thread(
            target=self._work_loop, name="key-worker", daemon=True
        ).start()

    # -- queue plumbing -----------------------------------------------------

    def dispatch(self, keycode: str, *, combo: bool) -> None:
        """Queues a keypad action; returns immediately."""
        handler = self._handle_combo_key if combo else self._handle_single_key
        self._queue.put(functools.partial(handler, keycode))

    def _work_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception:
                logger.exception("Unhandled error in queued action.")
            callback = self.on_action
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("on_action callback failed.")

    def run_action(self, fn: Callable[..., object], *args, timeout: float = 60.0, **kwargs):
        """Runs an action on the worker thread and waits for its result.

        Used by the web interface so its actions are serialized with keypad
        presses and benefit from the same bulb-rediscovery retry. The result
        is returned to the caller, and any error is re-raised (unlike the
        keypad path, which only logs) so the HTTP layer can report it.
        """
        box: dict[str, object] = {}
        done = threading.Event()

        def job() -> None:
            try:
                box["result"] = self._with_discovery_retry(fn, *args, **kwargs)
            except BaseException as e:  # noqa: BLE001 — relayed to the HTTP caller
                box["error"] = e
            finally:
                done.set()

        self._queue.put(job)
        if not done.wait(timeout):
            raise TimeoutError(f"Action {getattr(fn, '__name__', fn)} timed out after {timeout}s.")
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box.get("result")

    @staticmethod
    def _with_discovery_retry(fn: Callable[..., object], *args, max_attempts: int = 2, **kwargs):
        """Calls ``fn``, rediscovering bulb IPs once before a final retry."""
        for attempt in range(1, max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("%s failed (attempt %d/%d).", getattr(fn, "__name__", fn), attempt, max_attempts)
                if attempt == max_attempts:
                    raise
                yeelight_bulb_utils.update_discover_config()
                time.sleep(1)

    def state(self) -> dict[str, object]:
        """Returns light-related controller state for the web dashboard."""
        index = self._color_cycle_index % len(COLOR_CYCLE)
        return {
            "party_running": yeelight_bulb_utils.is_party_running(),
            "party_pattern": yeelight_bulb_utils.get_party_pattern().name,
            "color_cycle": COLOR_CYCLE[index],
            "color_cycle_index": index,
            "undo_depth": yeelight_bulb_utils.undo_depth(),
            "last_mode": self._last_mode,
        }

    # -- shared light actions (keypad + web) --------------------------------

    def _do_party_toggle(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            logger.info("Stopping party dance.")
            yeelight_bulb_utils.stop_party_dance()
        else:
            logger.info("Starting party dance.")
            yeelight_bulb_utils.save_state(bulbs)
            yeelight_bulb_utils.start_party_dance(bulbs)

    def _do_apply_mode(self, mode: yeelight_bulb_utils.BulbMode) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Setting mode: %s.", mode.name)
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.apply_mode(bulbs, mode)
        self._last_mode = mode.name

    def _do_color_cycle_start(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        self._color_cycle_index = 0
        color = COLOR_CYCLE[self._color_cycle_index]
        logger.info("Color cycle: %s (1/%d).", color, len(COLOR_CYCLE))
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.apply_mode(bulbs, color)

    def _do_color_cycle_advance(self, direction: int) -> None:
        """Steps the color cycle, or the dance pattern while party is active."""
        if yeelight_bulb_utils.is_party_running():
            if direction >= 0:
                yeelight_bulb_utils.next_party_pattern()
            else:
                yeelight_bulb_utils.prev_party_pattern()
            return
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        self._color_cycle_index = (self._color_cycle_index + direction) % len(COLOR_CYCLE)
        color = COLOR_CYCLE[self._color_cycle_index]
        logger.info(
            "Color cycle → %s: %s (%d/%d).",
            "next" if direction >= 0 else "prev",
            color, self._color_cycle_index + 1, len(COLOR_CYCLE),
        )
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.apply_mode(bulbs, color)

    def _do_party_pattern(self, index: int) -> None:
        """Web-only: jump straight to a dance pattern by index."""
        yeelight_bulb_utils.set_party_pattern(index)

    def _do_brightness(self, step: int) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Brightness step %+d.", step)
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.adjust_brightness(bulbs, step)

    def _do_all_off(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("All bulbs off.")
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.full_off(bulbs)
        self._last_mode = None

    def _do_random_color(self, *, each: bool) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Random color (%s).", "each different" if each else "all same")
        yeelight_bulb_utils.save_state(bulbs)
        if each:
            yeelight_bulb_utils.set_random_color_each(bulbs)
        else:
            yeelight_bulb_utils.set_random_color(bulbs)

    def _do_undo(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        logger.info("Undo — restore previous state.")
        yeelight_bulb_utils.restore_state(bulbs)

    def _do_control(
        self,
        targets: str | None,
        *,
        power: bool | None = None,
        brightness: int | None = None,
        color: str | None = None,
    ) -> dict[str, object]:
        """Web-only direct control: power, brightness and arbitrary colors.

        Stops the party dance first (it would otherwise overwrite the change)
        but does not push an undo snapshot, so dragging a slider doesn't flood
        the undo stack.
        """
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running() and (
            power is not None or brightness is not None or color is not None
        ):
            yeelight_bulb_utils.stop_party_dance()
        return yeelight_bulb_utils.control_bulbs(
            targets,
            power=power,
            brightness=brightness,
            color=color,
            registered=bulbs,
        )

    def _do_capture(self, *, flash: bool) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Capture (flash=%s).", flash)
        _capture(bulbs, flash=flash)

    def _do_countdown_capture(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Countdown then capture with flash.")
        _countdown_capture_with_flash(bulbs)

    def _do_vacuum_toggle(self) -> None:
        """Keypad KP1: start a full-speed sweep, or stop and dock if cleaning."""
        try:
            vac = xiaomi_vacuum_utils.get_device()
            raw_status = xiaomi_vacuum_utils._get_prop(vac, "status")
            cleaning = raw_status in (
                xiaomi_vacuum_utils.Status.SWEEPING,
                xiaomi_vacuum_utils.Status.SWEEPING_AND_MOPPING,
                xiaomi_vacuum_utils.Status.MOPPING,
                xiaomi_vacuum_utils.Status.PAUSED,
            )
            if cleaning:
                logger.info("KP1 — vacuum: stop and return to dock.")
                xiaomi_vacuum_utils.stop(vac)
                xiaomi_vacuum_utils.return_dock(vac)
            else:
                logger.info("KP1 — vacuum: sweep at full speed.")
                xiaomi_vacuum_utils.set_suction_level(xiaomi_vacuum_utils.SuctionLevel.FULL_SPEED, vac)
                xiaomi_vacuum_utils.start_sweep(vac)
        except Exception as e:
            xiaomi_vacuum_utils.reset_device()
            logger.error("Vacuum command failed: %s", e)

    # -- keypad dispatch ----------------------------------------------------

    @retry_with_discovery(max_attempts=3)
    def _handle_single_key(self, keycode: str) -> None:
        """Dispatches standalone (non-combo) key actions."""
        if keycode == "KEY_KP4":
            self._do_party_toggle()
            return

        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()

        if keycode in _SINGLE_KEY_MODES:
            self._do_apply_mode(_SINGLE_KEY_MODES[keycode])
        elif keycode == "KEY_KP5":
            self._do_color_cycle_start()
        elif keycode == "KEY_KPPLUS":
            self._do_brightness(_BRIGHTNESS_STEP)
        elif keycode == "KEY_KPMINUS":
            self._do_brightness(-_BRIGHTNESS_STEP)
        elif keycode == "KEY_KP1":
            self._do_vacuum_toggle()

    @retry_with_discovery(max_attempts=3)
    def _handle_combo_key(self, keycode: str) -> None:
        """Dispatches KP0 + key combo actions."""
        if keycode == "KEY_KP6":
            self._do_color_cycle_advance(1)
            return
        if keycode == "KEY_KP4":
            self._do_color_cycle_advance(-1)
            return

        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()

        if keycode in _COMBO_KEY_MODES:
            self._do_apply_mode(_COMBO_KEY_MODES[keycode])
        elif keycode == "KEY_KPMINUS":
            self._do_all_off()
        elif keycode == "KEY_KP1":
            self._do_capture(flash=False)
        elif keycode == "KEY_KP2":
            self._do_capture(flash=True)
        elif keycode == "KEY_KP3":
            self._do_countdown_capture()
        elif keycode == "KEY_KPASTERISK":
            self._do_random_color(each=False)
        elif keycode == "KEY_KPSLASH":
            self._do_random_color(each=True)
        elif keycode == "KEY_BACKSPACE":
            self._do_undo()


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Coukab LAN home automation controller.",
    )
    parser.add_argument(
        "--device",
        default="/dev/input/event3",
        help="Input device path (find yours via `sudo evtest`).",
    )
    parser.add_argument(
        "--grab",
        action="store_true",
        help="Grab the input device exclusively to prevent key passthrough.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the LAN web control interface.",
    )
    parser.add_argument(
        "--web-host",
        default="0.0.0.0",
        help="Web interface bind address (default: all interfaces).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Web interface port (default: 8080).",
    )
    return parser.parse_args()


def run_keyboard(
    device_path: str,
    controller: Controller,
    *,
    grab: bool = False,
    required: bool = True,
) -> bool:
    """Opens the input device and loops on keyboard events.

    Returns True if the keypad ran, False if the device was unavailable and
    ``required`` is False (so the caller can keep the web interface alive).
    Exits the process when the device is unavailable and ``required`` is True.
    """
    try:
        device = evdev.InputDevice(device_path)
    except (FileNotFoundError, PermissionError) as e:
        logger.error("Cannot open %s: %s", device_path, e)
        if required:
            sys.exit(1)
        logger.warning("Keypad unavailable — continuing with the web interface only.")
        return False

    logger.info("Listening on %s (%s).", device_path, device.name)

    if grab:
        device.grab()
        logger.info("Device grabbed exclusively.")

    kp0_held = False

    try:
        for event in device.read_loop():
            if event.type != evdev.ecodes.EV_KEY:
                continue
            key_event = evdev.categorize(event)
            keycode = key_event.keycode
            keystate = key_event.keystate

            if keycode == "KEY_KP0":
                kp0_held = keystate != _KEY_UP
                continue

            if keystate != _KEY_DOWN:
                continue

            controller.dispatch(keycode, combo=kp0_held)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        if grab:
            try:
                device.ungrab()
            except OSError:
                pass
        logger.info("Stopped.")
    return True


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    controller = Controller()

    web = None
    if not args.no_web:
        import web_server
        try:
            web = web_server.start(controller, host=args.web_host, port=args.web_port)
        except OSError as e:
            logger.error("Could not start web interface on %s:%d — %s", args.web_host, args.web_port, e)

    try:
        ran = run_keyboard(args.device, controller, grab=args.grab, required=web is None)
        if not ran and web is not None:
            # Keypad unavailable but the web interface is up — stay alive for it.
            logger.info("Web-only mode. Press Ctrl+C to stop.")
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                logger.info("Interrupted.")
    finally:
        if web is not None:
            web.shutdown()


if __name__ == "__main__":
    main()
