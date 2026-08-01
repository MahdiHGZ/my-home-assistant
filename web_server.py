"""LAN web control interface for Coukab.

A dependency-free HTTP server (Python standard library only) that serves a
single-page UI and a small JSON API. It is started from ``main.py`` so the
keypad listener and the web interface run from one process.

Design notes:
- Light actions that touch shared state (the party-dance thread, the
  color-cycle index) are routed through ``Controller.run_action`` so they are
  serialized with keypad presses. Vacuum, purifier and camera calls are
  independent devices and run directly on the request thread.
- There is no authentication by design — bind only to a trusted LAN.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import socket
import threading
import time
from urllib.parse import parse_qs

from dotenv import load_dotenv
from concurrent.futures import Future, ThreadPoolExecutor, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tapo_camera_utils as tapo
import tuch_controller_utils as panel
import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
import yeelight_bulb_utils as yeelight

load_dotenv()

# Optional protection for destructive actions. When set in .env, deleting a
# captured image requires this password; when unset, deletion is open (the
# default for a trusted home LAN).
_DELETE_PASSWORD = (os.getenv("MOMENTS_DELETE_PASSWORD") or "").strip()

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Matches the keypad brightness step in main.py so the web +/- buttons behave
# identically to the keypad.
_BRIGHTNESS_STEP = 20

# Quick-access lighting scenes shown as buttons (subset of yeelight MODES).
_SCENE_MODE_KEYS = ["cool_white", "warm_white", "sunset", "sleep", "romantic", "movie"]

# Reused across status requests so each request reads the three devices in
# parallel with a bound on total threads.
_STATUS_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="web-status")
_STATUS_TIMEOUT_S = 12

# Vacuum commands share one MIoT transport. Serialize them so request threads
# cannot interleave command/response traffic. Remote-drive requests additionally
# carry a browser-session sequence: once STOP(N) is seen, any delayed direction
# with a sequence <= N is discarded instead of restarting physical movement.
_vacuum_action_lock = threading.Lock()
_remote_latest_seq: dict[str, int] = {}
_REMOTE_SESSION_LIMIT = 64

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

# Files served from the site root (a service worker must live at the root
# to control the whole origin).
_ROOT_STATIC = {
    "/sw.js": "sw.js",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/icon.svg": "icon.svg",
}

# -- live updates (Server-Sent Events) ----------------------------------------
#
# Browsers subscribe to /api/events. Whenever an action completes (keypad or
# web), notify_status_changed() wakes a single notifier thread, which builds
# one fresh status snapshot (debounced) and broadcasts it to every subscriber.
# Heartbeat comments keep idle connections alive through proxies.

_SSE_HEARTBEAT_S = 15
_SSE_DEBOUNCE_S = 0.3

_sse_clients: set[queue.Queue] = set()
_sse_lock = threading.Lock()
_status_dirty = threading.Event()
_controller_ref = None  # set by start()

_STATUS_CACHE_TTL_S = 3.0
_last_status_time = 0.0
_status_invalidated = True


def notify_status_changed() -> None:
    """Schedules a status push to all connected SSE clients and invalidates cache."""
    global _status_invalidated
    with _last_status_lock:
        _status_invalidated = True
    _status_dirty.set()


def _sse_broadcast(event: str, payload: str) -> None:
    """Queues one named SSE event for every connected client."""
    with _sse_lock:
        clients = list(_sse_clients)
    for q in clients:
        try:
            q.put_nowait((event, payload))
        except queue.Full:
            pass  # client is backed up; it will catch up on the next push


def _notifier_loop() -> None:
    while True:
        _status_dirty.wait()
        time.sleep(_SSE_DEBOUNCE_S)  # coalesce action bursts into one snapshot
        _status_dirty.clear()
        with _sse_lock:
            has_clients = bool(_sse_clients)
        if not has_clients or _controller_ref is None:
            continue
        try:
            payload = json.dumps(_cached_status(_controller_ref))
        except Exception:
            logger.exception("Status build for SSE push failed.")
            continue
        _sse_broadcast("status", payload)

# -- optional LLM assistant ---------------------------------------------------

_chat_lock = threading.Lock()
_chat_ready = False
# Single-shot by design: each request is one command -> one answer, with NO
# memory of prior messages. This keeps the system+tools prompt prefix
# identical every time so the model's RAM prompt-cache can skip re-prefilling
# it — a big win on the 2-core CPU server.


def _chat_available() -> bool:
    """True if the local LLM brain can run (library installed + model present)."""
    try:
        import brain
    except Exception:
        return False
    if brain.Llama is None:
        return False
    return Path(brain.DEFAULT_MODEL_PATH).exists()


def _ensure_chat_ready():
    """Lazily load the model + register tools once; returns the brain module."""
    global _chat_ready
    import brain
    import brain_test
    if not _chat_ready:
        logger.info("Loading assistant model (first chat request)…")
        brain.load_model()
        brain_test.register_all_tools()
        _chat_ready = True
    return brain


def _run_chat(message: str) -> str:
    """Answer one isolated command without sharing data across clients."""
    with _chat_lock:
        brain = _ensure_chat_ready()
        return brain.run_prompt(
            message,
            system_prompt=brain.get_default_system_prompt(),
            use_tools=True,
        )


def _run_chat_stream(message: str):
    """Yield events for one isolated command."""
    with _chat_lock:
        brain = _ensure_chat_ready()
        for event in brain.run_prompt_stream(
            message,
            system_prompt=brain.get_default_system_prompt(),
            use_tools=True,
        ):
            yield event


# -- status helpers -----------------------------------------------------------

def _safe(fn) -> dict:
    """Run a status reader, normalizing the result and trapping failures."""
    try:
        data = fn()
        result = dict(data) if isinstance(data, dict) else {"value": data}
        result["available"] = True
        return result
    except Exception as e:
        return {"available": False, "error": str(e)}


def _lights_status() -> dict:
    res = yeelight.control_bulbs()
    return {
        "ok": res.get("ok"),
        "bulbs": res.get("bulbs", []),
        "registered_names": res.get("registered_names", []),
    }


def _vacuum_status() -> dict:
    vacuum.validate_config()
    return vacuum.get_status()


def _purifier_status() -> dict:
    purifier.validate_config()
    return purifier.get_status()


def _vacuum_consumables() -> dict:
    vacuum.validate_config()
    return vacuum.get_consumables()


def _parse_room_list(raw) -> list[dict]:
    """Best-effort parse of the vacuum's room list (format varies by firmware).

    Accepts miio action-result dicts, JSON strings, ``"1:Living,2:Kitchen"``
    strings, or plain id lists. Returns ``[{"id", "name"}, ...]``; empty when
    nothing recognizable was found (the UI then hides the rooms section).
    """
    value = raw
    if isinstance(value, dict):
        out = value.get("out")
        if isinstance(out, (list, tuple)) and out:
            value = out[0]
            if isinstance(value, dict) and "value" in value:
                value = value["value"]

    rooms: list[dict] = []

    def _add(rid, name=None) -> None:
        rid = str(rid).strip()
        if rid:
            rooms.append({"id": rid, "name": str(name).strip() if name else f"Room {rid}"})

    items = None
    if isinstance(value, str) and value.strip():
        try:
            items = json.loads(value)
        except json.JSONDecodeError:
            for piece in value.split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if ":" in piece:
                    rid, _, name = piece.partition(":")
                    _add(rid, name)
                elif piece.isdigit():
                    _add(piece)
            return rooms
    elif isinstance(value, (list, dict)):
        items = value

    if isinstance(items, dict):
        items = items.get("rooms", items)
    if isinstance(items, dict):
        for rid, name in items.items():
            _add(rid, name)
    elif isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                rid = item.get("id") or item.get("roomId") or item.get("room_id")
                name = item.get("name") or item.get("roomName") or item.get("room_name")
                if rid is not None:
                    _add(rid, name)
            elif isinstance(item, (list, tuple)) and item:
                _add(item[0], item[1] if len(item) > 1 else None)
            elif isinstance(item, (int, str)):
                _add(item)
    return rooms


def _vacuum_rooms() -> dict:
    vacuum.validate_config()
    raw = vacuum.get_room_list()
    return {"rooms": _parse_room_list(raw)}


def _moment_files() -> list[Path]:
    moments_dir = Path(tapo.MOMENTS_DIR).resolve()
    if not moments_dir.is_dir():
        return []
    files = [
        p for p in moments_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    
    return files


def _moments_summary() -> dict:
    files = _moment_files()
    recent = [f"/moments/{p.name}" for p in files[:12]]
    return {
        "count": len(files),
        "latest": recent[0] if recent else None,
        "recent": recent,
    }


def _resolve_moment(name: str, thumb: bool = False) -> Path:
    """Resolve a moment file name to a path inside the moments dir, or raise.

    Guards against path traversal and rejects non-image / missing files.
    """
    moments_dir = Path(tapo.MOMENTS_DIR).resolve()
    
    path = (moments_dir / ".thumbs" / name).resolve() if thumb else (moments_dir / name).resolve()
    if thumb and not path.is_file():
        path = (moments_dir / name).resolve()

    if moments_dir not in path.parents:
        raise _ApiError("Invalid file name.", status=400)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not path.is_file():
        raise _ApiError("No such image.", status=404)
    return path


def _delete_moment(name: str) -> None:
    """Deletes one captured image (validated by :func:`_resolve_moment`)."""
    path = _resolve_moment(name)
    path.unlink()
    thumb_path = path.parent / ".thumbs" / path.name
    if thumb_path.exists():
        thumb_path.unlink()
    logger.info("Moment deleted via web: %s", path.name)


# Most recent status snapshot, kept so cheap clients (e.g. the ESP32 touch
# panel) can sync instantly from /api/status/cached instead of waiting for
# a fresh multi-device poll. Updated by every _build_status() call (direct
# /api/status requests and SSE pushes alike).
_last_status_lock = threading.Lock()
_last_status: dict | None = None
_status_refresh_lock = threading.Lock()
_status_futures_lock = threading.Lock()
_status_futures: dict[str, Future] = {}


def _status_snapshot(snapshot: dict, generated_at: float) -> dict:
    """Return a copy annotated with cache age without mutating the cache."""
    age = max(0.0, time.monotonic() - generated_at)
    result = dict(snapshot)
    result["_meta"] = {
        "age_ms": round(age * 1000),
        "stale": age >= _STATUS_CACHE_TTL_S,
    }
    return result


def _device_status_futures() -> dict[str, Future]:
    readers = {
        "lights": _lights_status,
        "vacuum": _vacuum_status,
        "purifier": _purifier_status,
    }
    with _status_futures_lock:
        for key, reader in readers.items():
            future = _status_futures.get(key)
            if future is None or future.done():
                _status_futures[key] = _STATUS_POOL.submit(_safe, reader)
        return dict(_status_futures)


def _build_status(controller) -> dict:
    global _last_status, _last_status_time, _status_invalidated
    requested_at = time.monotonic()
    with _status_refresh_lock:
        # A concurrent builder may already have produced the snapshot this
        # caller needed; reuse it instead of immediately polling again.
        with _last_status_lock:
            if _last_status is not None and _last_status_time >= requested_at:
                return _status_snapshot(_last_status, _last_status_time)

        futures = _device_status_futures()
        done, _ = wait(set(futures.values()), timeout=_STATUS_TIMEOUT_S)
        result: dict = {}
        for key, future in futures.items():
            if future not in done:
                result[key] = {
                    "available": False,
                    "error": "device did not respond in time",
                }
                continue
            try:
                result[key] = future.result()
            except Exception as e:  # noqa: BLE001
                result[key] = {"available": False, "error": str(e)}
        result["lights"]["state"] = controller.state()
        result["moments"] = _moments_summary()
        generated_at = time.monotonic()
        with _last_status_lock:
            _last_status = result
            _last_status_time = generated_at
            _status_invalidated = False
        return _status_snapshot(result, generated_at)


def _cached_status(controller) -> dict:
    """Returns the cached status if it's less than TTL seconds old, else builds fresh."""
    global _last_status, _last_status_time
    now = time.monotonic()
    with _last_status_lock:
        if (
            _last_status is not None
            and not _status_invalidated
            and (now - _last_status_time) < _STATUS_CACHE_TTL_S
        ):
            return _status_snapshot(_last_status, _last_status_time)
    return _build_status(controller)


def _panel_cached_status(controller) -> dict:
    """Return panel status immediately when any snapshot exists.

    The ESP32 polls this on wake/SSE pokes and has a tiny HTTP timeout. A stale
    but recent-looking panel is better than blocking behind slow vacuum/purifier
    reads; the SSE notifier refreshes the cache in the background.
    """
    global _last_status, _last_status_time
    now = time.monotonic()
    with _last_status_lock:
        if _last_status is not None:
            if (now - _last_status_time) >= _STATUS_CACHE_TTL_S:
                _status_dirty.set()
            return _status_snapshot(_last_status, _last_status_time)
    return _build_status(controller)


# -- panel API (ESP32 touch controller) ----------------------------------------
#
# The touch panel is a constrained client: it wants a tiny, flat, stable
# payload instead of the full browser snapshot, and display-ready pixels
# instead of JPEGs. Everything here is additive; the browser endpoints are
# untouched. See tuch_controller/HARDWARE.md (section 5) for the contract.

# The flattening, RGB565 conversion, alert store/render, and panel discovery
# all live in tuch_controller_utils (`panel`). These wrappers add the
# HTTP-layer concerns: the cached snapshot, moment file resolution, and
# mapping errors to _ApiError status codes.

def _panel_status(controller) -> dict:
    """Flat ~250-byte summary of the cached status for the touch panel."""
    return panel.flatten_status(_panel_cached_status(controller))


def _panel_moment_rgb565(name: str | None, w: int, h: int) -> bytes:
    """Renders a moment to raw big-endian RGB565 (w*h*2 bytes) for the TFT."""
    import cv2          # lazy: heavy import, already a project dependency

    if name:
        path = _resolve_moment(name.rsplit("/", 1)[-1])
    else:
        files = _moment_files()
        if not files:
            raise _ApiError("No moments captured yet.", status=404)
        path = files[0]

    w = max(16, min(320, w))
    h = max(16, min(240, h))

    img = cv2.imread(str(path))  # BGR
    if img is None:
        raise _ApiError("Could not decode image.", status=500)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return panel.bgr_to_rgb565_bytes(img)


def _panel_alert_send(body: dict) -> dict:
    """Stores an alert (panel module) and pushes an SSE event to all clients."""
    text = _require(body, "text")
    try:
        return panel.send_alert(str(text), body.get("level"))
    except ValueError as e:
        raise _ApiError(str(e)) from e


def _panel_alert_get() -> dict:
    return panel.get_alert()


def _panel_alert_rgb565(w: int, h: int) -> bytes:
    """Server-rendered alert popup; 404 when there is nothing to show."""
    data = panel.render_alert_rgb565(w, h)
    if data is None:
        raise _ApiError("No alert to render.", status=404)
    return data


def _enum_options(enum_cls) -> list[dict]:
    return [
        {"value": int(member.value), "name": member.name.replace("_", " ").title()}
        for member in enum_cls
    ]


def _capabilities() -> dict:
    try:
        bulbs = sorted(yeelight.load_registered_bulbs().keys())
    except Exception:
        bulbs = []
    return {
        "chat": _chat_available(),
        "chat_ready": _chat_ready,
        "delete_protected": bool(_DELETE_PASSWORD),
        "bulbs": bulbs,
        "color_cycle": list(yeelight.COLOR_CYCLE),
        "scene_modes": [
            {"key": k, "name": yeelight.MODES[k].name}
            for k in _SCENE_MODE_KEYS if k in yeelight.MODES
        ],
        "dance_patterns": [p.name for p in yeelight.DANCE_PATTERNS],
        "suction_levels": _enum_options(vacuum.SuctionLevel),
        "water_levels": _enum_options(vacuum.WaterLevel),
        "purifier_modes": _enum_options(purifier.Mode),
        "fan_levels": _enum_options(purifier.FanLevel),
        "screen_brightness": _enum_options(purifier.ScreenBrightness),
        "favorite_speed": {"min": 200, "max": 2300, "step": 50},
        "volume": {"min": 0, "max": 10, "step": 1},
    }


# -- action handlers ----------------------------------------------------------

class _ApiError(Exception):
    """Raised to return a 4xx JSON error to the client."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _require(body: dict, key: str):
    if key not in body or body[key] in (None, ""):
        raise _ApiError(f"Missing '{key}'.")
    return body[key]


def _lights_action(controller, body: dict) -> dict:
    action = _require(body, "action")
    c = controller
    if action == "mode":
        key = _require(body, "mode")
        if key not in yeelight.MODES:
            raise _ApiError(f"Unknown mode '{key}'.")
        c.run_action(c._do_apply_mode, yeelight.MODES[key])
    elif action == "full_bright":
        c.run_action(c._do_apply_mode, yeelight.MODES["max_bright"])
    elif action == "party_toggle":
        c.run_action(c._do_party_toggle)
    elif action == "party_pattern":
        c.run_action(c._do_party_pattern, int(_require(body, "value")))
    elif action == "cycle_start":
        c.run_action(c._do_color_cycle_start)
    elif action == "cycle_next":
        c.run_action(c._do_color_cycle_advance, 1)
    elif action == "cycle_prev":
        c.run_action(c._do_color_cycle_advance, -1)
    elif action == "brightness_up":
        c.run_action(c._do_brightness, _BRIGHTNESS_STEP)
    elif action == "brightness_down":
        c.run_action(c._do_brightness, -_BRIGHTNESS_STEP)
    elif action == "all_off":
        c.run_action(c._do_all_off)
    elif action == "random_color":
        c.run_action(c._do_random_color, each=False)
    elif action == "random_each":
        c.run_action(c._do_random_color, each=True)
    elif action == "undo":
        c.run_action(c._do_undo)
    else:
        raise _ApiError(f"Unknown light action '{action}'.")
    return {"ok": True, "state": c.state()}


def _lights_control(controller, body: dict) -> dict:
    targets = body.get("targets") or "all"
    power = body.get("power")
    brightness = body.get("brightness")
    color = body.get("color")
    if power is None and brightness is None and color is None:
        raise _ApiError("Provide at least one of power, brightness, color.")
    if brightness is not None:
        brightness = max(1, min(100, int(brightness)))
    result = controller.run_action(
        controller._do_control,
        targets,
        power=power,
        brightness=brightness,
        color=color,
    )
    return {"ok": bool(result.get("ok")), "result": result, "state": controller.state()}


def _vacuum_action(body: dict) -> dict:
    action = _require(body, "action")
    value = body.get("value")
    try:
        with _vacuum_action_lock:
            if action == "sweep":
                vacuum.start_sweep()
            elif action == "mop":
                vacuum.start_mop()
            elif action == "sweep_mop":
                vacuum.start_sweep_mop()
            elif action == "stop":
                vacuum.stop()
            elif action == "pause":
                vacuum.pause()
            elif action == "dock":
                vacuum.return_dock()
            elif action == "find_me":
                vacuum.find_me()
            elif action == "suction":
                vacuum.set_suction_level(int(value))
            elif action == "water":
                vacuum.set_water_level(int(value))
            elif action == "volume":
                vacuum.set_volume(int(value))
            elif action == "room_sweep":
                vacuum.start_room_sweep(str(_require(body, "value")))
            elif action == "remote":
                direction = int(value)
                session = body.get("session")
                seq = body.get("seq")
                if session is not None or seq is not None:
                    if not isinstance(session, str) or not session.strip():
                        raise _ApiError("Remote control requires a non-empty 'session'.")
                    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
                        raise _ApiError("Remote control requires a positive integer 'seq'.")
                    session = session[:80]
                    latest = _remote_latest_seq.get(session, 0)
                    if seq <= latest:
                        return {"ok": True, "ignored": True, "reason": "stale remote command"}
                    if session not in _remote_latest_seq and len(_remote_latest_seq) >= _REMOTE_SESSION_LIMIT:
                        _remote_latest_seq.pop(next(iter(_remote_latest_seq)))
                    # Record intent before I/O. If STOP fails, older movement
                    # still must not be allowed to run afterward.
                    _remote_latest_seq[session] = seq
                vacuum.remote_control(direction)
            else:
                raise _ApiError(f"Unknown vacuum action '{action}'.")
    except _ApiError:
        raise
    except Exception as e:
        vacuum.reset_device()
        raise _ApiError(f"Vacuum command failed: {e}", status=502) from e
    notify_status_changed()
    return {"ok": True}


def _purifier_action(body: dict) -> dict:
    action = _require(body, "action")
    value = body.get("value")
    try:
        if action == "power":
            purifier.turn_on() if value else purifier.turn_off()
        elif action == "toggle":
            purifier.toggle()
        elif action == "mode":
            purifier.set_mode(int(value))
        elif action == "fan":
            purifier.set_fan_level(int(value))
        elif action == "manual_level":
            purifier.set_manual_level(int(value))
        elif action == "favorite_speed":
            purifier.set_favorite_speed(int(value))
        elif action == "anion":
            purifier.set_anion(bool(value))
        elif action == "child_lock":
            purifier.set_child_lock(bool(value))
        elif action == "buzzer":
            purifier.set_buzzer(bool(value))
        elif action == "screen":
            purifier.set_screen_brightness(int(value))
        else:
            raise _ApiError(f"Unknown purifier action '{action}'.")
    except _ApiError:
        raise
    except Exception as e:
        purifier.reset_device()
        raise _ApiError(f"Purifier command failed: {e}", status=502) from e
    notify_status_changed()
    return {"ok": True}


def _camera_capture(controller, body: dict) -> dict:
    flash = bool(body.get("flash"))
    try:
        if flash:
            captured = controller.run_action(controller._do_capture, flash=True, timeout=90)
        else:
            result = tapo.capture_moment_for_model(include_data_uri=False)
            captured = result["image_path"]
    except Exception as e:
        raise _ApiError(f"Capture failed: {e}", status=502) from e
    image_path = Path(captured)
    if not image_path.is_file():
        raise _ApiError("Capture produced no image.", status=502)
    notify_status_changed()
    return {"ok": True, "image": f"/moments/{image_path.name}"}


def _camera_delete(body: dict) -> dict:
    """Deletes a single captured image by its `/moments/<name>` URL or name."""
    if _DELETE_PASSWORD:
        supplied = str(body.get("password") or "")
        if not hmac.compare_digest(supplied, _DELETE_PASSWORD):
            raise _ApiError("Wrong or missing delete password.", status=403)
    raw = str(_require(body, "image"))
    name = raw.rsplit("/", 1)[-1]  # accept a full /moments/<name> URL or bare name
    _delete_moment(name)
    notify_status_changed()
    return {"ok": True, **_moments_summary()}


def _chat(body: dict) -> dict:
    message = _require(body, "message")
    if not _chat_available():
        return {"ok": False, "error": "Assistant is unavailable (LLM model not installed)."}
    try:
        reply = _run_chat(str(message))
    except Exception as e:
        raise _ApiError(f"Assistant error: {e}", status=502) from e
    return {"ok": True, "reply": reply}


# -- HTTP handler -------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "Coukab/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def _controller(self):
        return self.server.controller  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - base API
        logger.debug("web %s - %s", self.address_string(), fmt % args)

    # -- response helpers --
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise _ApiError(f"Invalid JSON body: {e}") from e
        if not isinstance(data, dict):
            raise _ApiError("Request body must be a JSON object.")
        return data

    def _serve_static(self, name: str) -> None:
        path = (WEB_DIR / name).resolve()
        if WEB_DIR not in path.parents or not path.is_file():
            self._send_json({"error": "Not found."}, status=404)
            return
        content_type = _STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # The service worker must revalidate every load so UI updates roll
        # out promptly; other assets may be briefly cached.
        if name == "sw.js":
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_rgb565(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _query_dims(query: str, dw: int, dh: int) -> tuple[int, int]:
        q = parse_qs(query)
        try:
            return int((q.get("w") or [dw])[0]), int((q.get("h") or [dh])[0])
        except ValueError as e:
            raise _ApiError(f"Bad dimensions: {e}") from e

    def _serve_panel_thumb(self, query: str) -> None:
        """Raw RGB565 thumbnail of a moment for the ESP32 panel."""
        name = (parse_qs(query).get("name") or [None])[0]
        w, h = self._query_dims(query, 296, 186)
        self._send_rgb565(_panel_moment_rgb565(name, w, h))

    def _serve_panel_alert_img(self, query: str) -> None:
        """Raw RGB565 popup image of the current alert for the ESP32 panel."""
        w, h = self._query_dims(query, 296, 150)
        self._send_rgb565(_panel_alert_rgb565(w, h))

    def _serve_chat_stream(self, body: dict) -> None:
        """Stream the assistant's reply as newline-delimited JSON events.

        Each line is one JSON object: {"type": "status"|"token"|"done"|"error",
        "text": ...}. The browser reads the chunks live so the slow CPU model
        feels responsive. Tools still run server-side between rounds.
        """
        message = str(body.get("message") or "").strip()
        if not message:
            self._send_json({"ok": False, "error": "Empty message."}, status=400)
            return
        if not _chat_available():
            self._send_json(
                {"ok": False, "error": "Assistant is unavailable (LLM model not installed)."},
                status=503,
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def write(event: dict) -> None:
            self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            for event in _run_chat_stream(message):
                write(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client navigated away mid-reply
        except Exception as e:  # noqa: BLE001
            logger.exception("Chat stream failed")
            try:
                write({"type": "error", "text": str(e)})
            except OSError:
                pass

    def _serve_events(self) -> None:
        """Long-lived SSE stream: pushes status snapshots + heartbeats."""
        # The firmware tags its request so we can learn the panel's LAN IP.
        if self.headers.get("X-Coukab-Panel"):
            panel.register_panel(self.client_address[0])
        client: queue.Queue = queue.Queue(maxsize=4)
        with _sse_lock:
            _sse_clients.add(client)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            # Initial snapshot so a freshly opened page renders at once.
            payload = json.dumps(_cached_status(self._controller))
            self.wfile.write(f"event: status\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    event, payload = client.get(timeout=_SSE_HEARTBEAT_S)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client went away
        finally:
            with _sse_lock:
                _sse_clients.discard(client)

    def _serve_moment(self, filename: str, *, download: bool = False, thumb: bool = False) -> None:
        try:
            path = _resolve_moment(filename, thumb=thumb)
        except _ApiError as e:
            self._send_json({"ok": False, "error": str(e)}, status=e.status)
            return
        content_type = _STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Timestamped file names are immutable, so allow caching.
        self.send_header("Cache-Control", "max-age=86400")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    # -- routing --
    def do_GET(self) -> None:  # noqa: N802 - base API
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path.removeprefix("/static/"))
            elif path in _ROOT_STATIC:
                self._serve_static(_ROOT_STATIC[path])
            elif path == "/api/capabilities":
                self._send_json(_capabilities())
            elif path == "/api/health":
                self._send_json({"ok": True, "status": "running"})
            elif path == "/api/status":
                self._send_json(_cached_status(self._controller))
            elif path == "/api/status/cached":
                self._send_json(_cached_status(self._controller))
            elif path == "/api/panel/status":
                self._send_json(_panel_status(self._controller))
            elif path == "/api/panel/moment.rgb565":
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                self._serve_panel_thumb(query)
            elif path == "/api/panel/alert":
                self._send_json(_panel_alert_get())
            elif path == "/api/panel/alert.rgb565":
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                self._serve_panel_alert_img(query)
            elif path == "/api/events":
                self._serve_events()
            elif path == "/api/vacuum/consumables":
                self._send_json(_safe(_vacuum_consumables))
            elif path == "/api/vacuum/rooms":
                self._send_json(_safe(_vacuum_rooms))
            elif path == "/api/moments":
                self._send_json(_moments_summary())
            elif path.startswith("/api/actions/"):
                job_id = path.removeprefix("/api/actions/")
                state = self._controller.job_state(job_id)
                if state is None:
                    raise _ApiError("No such action.", status=404)
                self._send_json({"ok": True, "job": state})
            elif path.startswith("/moments/"):
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                download = "download=1" in query or "download=true" in query
                thumb = "thumb=1" in query or "thumb=true" in query
                self._serve_moment(path.removeprefix("/moments/"), download=download, thumb=thumb)
            elif path == "/favicon.ico":
                self._send_bytes(b"", "image/x-icon", status=204)
            else:
                self._send_json({"error": "Not found."}, status=404)
        except _ApiError as e:
            self._send_json({"ok": False, "error": str(e)}, status=e.status)
        except Exception as e:  # noqa: BLE001
            logger.exception("GET %s failed", path)
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def do_POST(self) -> None:  # noqa: N802 - base API
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_body()
            if path == "/api/lights/action":
                self._send_json(_lights_action(self._controller, body))
            elif path == "/api/lights/control":
                self._send_json(_lights_control(self._controller, body))
            elif path == "/api/vacuum/action":
                self._send_json(_vacuum_action(body))
            elif path == "/api/purifier/action":
                self._send_json(_purifier_action(body))
            elif path == "/api/camera/capture":
                self._send_json(_camera_capture(self._controller, body))
            elif path == "/api/camera/delete":
                self._send_json(_camera_delete(body))
            elif path == "/api/panel/alert":
                self._send_json(_panel_alert_send(body))
            elif path == "/api/chat":
                self._send_json(_chat(body))
            elif path == "/api/chat/stream":
                self._serve_chat_stream(body)
            else:
                self._send_json({"error": "Not found."}, status=404)
        except _ApiError as e:
            self._send_json({"ok": False, "error": str(e)}, status=e.status)
        except Exception as e:  # noqa: BLE001
            logger.exception("POST %s failed", path)
            if e.__class__.__name__ in {"QueueFullError", "ActionQueueFullError"}:
                self._send_json({"ok": False, "error": "Too many requests. Please try again."}, status=429)
            elif e.__class__.__name__ == "ActionInProgressError":
                self._send_json(
                    {
                        "ok": True,
                        "accepted": True,
                        "job_id": e.job_id,
                        "status_url": f"/api/actions/{e.job_id}",
                    },
                    status=202,
                )
            elif isinstance(e, TimeoutError):
                self._send_json({"ok": False, "error": str(e)}, status=504)
            elif isinstance(e, yeelight.BulbBatchError):
                payload = {"ok": False, "error": str(e)}
                if e.result is not None:
                    payload["result"] = e.result
                self._send_json(payload, status=502)
            else:
                self._send_json({"ok": False, "error": str(e)}, status=500)


def lan_ip() -> str:
    """Best-effort primary LAN IP for printing a reachable URL."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def start(controller, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    """Bind and start the web server in a background thread; returns the server."""
    global _controller_ref
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.controller = controller  # type: ignore[attr-defined]
    httpd.daemon_threads = True

    # Live updates: every completed controller job (keypad or web) triggers a
    # debounced status push to all SSE subscribers.
    _controller_ref = controller
    controller.on_action = notify_status_changed
    panel.set_broadcaster(_sse_broadcast)  # let the panel module push alerts
    threading.Thread(target=_notifier_loop, name="sse-notifier", daemon=True).start()

    threading.Thread(target=httpd.serve_forever, name="web-server", daemon=True).start()
    logger.info(
        "Web interface: http://%s:%d  (open http://%s:%d on your phone) — no login.",
        host, port, lan_ip(), port,
    )
    return httpd
