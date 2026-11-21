"""LAN web control interface for Coukab.

A dependency-free HTTP server (Python standard library only) that serves a
single-page UI and a small JSON API. It is started from ``main.py`` so the
keypad listener and the web interface run from one process.

Design notes:
- Light actions that touch shared state (the party-dance thread, the
  color-cycle index) are routed through ``Controller.run_action`` so they are
  serialized with keypad presses. Vacuum, purifier and camera calls are
  independent devices and run directly on the request thread.
- The default trusted-LAN mode supports optional HTTP Basic authentication.
"""

from __future__ import annotations

from functools import lru_cache, partial, wraps

import base64
import binascii
import gzip
import hashlib
import hmac
import json
import logging
import os
import queue
import shutil
import socket
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs

from dotenv import load_dotenv
from concurrent.futures import Future, ThreadPoolExecutor, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from action_errors import ActionCancelledError, ActionInProgressError, ActionQueueFullError
from household import Household
from worker import Worker
from receipts import ReceiptError, Receipts, accepted
from device_actions import ACTIONS, describe
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
_MAX_JSON_BODY_BYTES = 16 * 1024
_CONNECTION_TIMEOUT_S = 15
_MAX_HANDLER_THREADS = 32

# Vacuum commands share one MIoT transport. Serialize them so request threads
# cannot interleave command/response traffic. Remote-drive requests additionally
# carry a browser-session sequence: once STOP(N) is seen, any delayed direction
# with a sequence <= N is discarded instead of restarting physical movement.
_vacuum_action_lock = threading.Lock()


class _RemoteDrive:
    """All transitions and device writes are serialized by _vacuum_action_lock."""

    def __init__(self):
        self.epoch = uuid.uuid4().hex
        self.owner = None
        self.seq = 0
        self.timer = None

    def revoke(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.owner = None
        self.seq = 0
        self.epoch = uuid.uuid4().hex

    def expire(self, epoch):
        with _vacuum_action_lock:
            if self.epoch != epoch or threading.current_thread() is not self.timer:
                return
            was_driving = self.owner is not None
            self.revoke()
            try:
                if was_driving:
                    vacuum.remote_control(5)
            finally:
                try:
                    vacuum.remote_control(10)
                finally:
                    notify_status_changed()

    def stop_on_shutdown(self):
        with _vacuum_action_lock:
            active = self.timer is not None or self.owner is not None
            self.revoke()
            if active:
                try:
                    vacuum.remote_control(5)
                finally:
                    vacuum.remote_control(10)

    def arm_timeout(self):
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(5, self.expire, args=(self.epoch,))
        self.timer.daemon = True
        self.timer.start()

    def command(self, body):
        direction = _int_field(body, "value", allowed={1, 2, 3, 4, 5, 10})
        session = _string_field(body, "session", required=False)
        seq = _int_field(body, "seq", required=False, minimum=1)
        epoch = _string_field(body, "epoch", required=False)
        if (session is None) != (seq is None):
            raise _ApiError("Remote control requires both session and seq.")
        stop = direction in {5, 10}
        if not stop and not session:
            raise _ApiError("Movement requires a sequenced drive session.")
        if session is not None and session != self.owner:
            # A hold may claim the current epoch once. A release can arrive
            # before its first direction and must retire that same epoch.
            if epoch != self.epoch and not (stop and epoch is None):
                return {"ok": True, "ignored": True, "epoch": self.epoch}
            self.revoke()
            self.owner = session
        if session is not None and seq <= self.seq:
            return {"ok": True, "ignored": True, "epoch": self.epoch}
        self.seq = seq or 0
        if stop:
            self.revoke()
        if direction != 10:
            self.arm_timeout()
        vacuum.remote_control(direction)
        notify_status_changed()
        return {"ok": True, "epoch": self.epoch}


_drive = _RemoteDrive()

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


class _EventMailbox:
    """One pending notification per event type for each subscriber."""

    def __init__(self):
        self._ready = threading.Condition()
        self._events = {}

    def put(self, event, payload):
        with self._ready:
            replaced = event in self._events
            self._events[event] = payload
            self._ready.notify()
            return replaced

    def get(self, timeout):
        with self._ready:
            if not self._ready.wait_for(lambda: bool(self._events), timeout):
                raise queue.Empty
            event = next(iter(self._events))
            return event, self._events.pop(event)


_sse_clients: set[_EventMailbox] = set()
_sse_lock = threading.Lock()
_sse_coalesced = 0
_sse_rejected = 0
_device_success = {}
_status_dirty = threading.Event()
_controller_ref = None  # set by start()

_STATUS_CACHE_TTL_S = 3.0
_last_status_time = 0.0
_status_invalidated = True
_status_generation = 0


def notify_status_changed() -> None:
    """Schedules a status push to all connected SSE clients and invalidates cache."""
    global _status_invalidated, _status_generation
    with _last_status_lock:
        _status_generation += 1
        _status_invalidated = True
    _status_dirty.set()


def _sse_broadcast(event: str, payload: str) -> None:
    """Queues one named SSE event for every connected client."""
    with _sse_lock:
        global _sse_coalesced
        for client in _sse_clients:
            _sse_coalesced += bool(client.put(event, payload))


def _notifier_loop(stopped=None) -> None:
    while stopped is None or not stopped.is_set():
        _status_dirty.wait()
        if stopped is not None and stopped.is_set():
            return
        time.sleep(_SSE_DEBOUNCE_S)  # coalesce action bursts into one snapshot
        _status_dirty.clear()
        if _controller_ref is None:
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


def _assistant_handler(prefix, func):
    """Coordinate web assistant mutations with other physical controls."""
    if func.__name__.startswith("get_") or func.__name__ == "look_around" or prefix == "panel":
        return func

    @wraps(func)
    def invoke(**kwargs):
        if prefix == "light":
            if all(kwargs.get(key) is None for key in ("power", "brightness", "color")):
                return func(**kwargs)

            def change_light():
                if yeelight.is_party_running():
                    yeelight.stop_party_dance()
                return func(**kwargs)

            return _controller_ref.run_action(change_light)

        try:
            if prefix == "vacuum":
                with _vacuum_action_lock:
                    if func.__name__.startswith("start") or func.__name__ in {"stop", "pause", "return_dock"}:
                        _drive.revoke()
                    return func(**kwargs)
            return func(**kwargs)
        finally:
            notify_status_changed()

    return invoke


def _ensure_chat_ready():
    """Lazily load the model + register tools once; returns the brain module."""
    global _chat_ready
    import brain
    import brain_test
    if not _chat_ready:
        logger.info("Loading assistant model (first chat request)…")
        brain.load_model()
        brain_test.register_all_tools(adapt=_assistant_handler)
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
    with tapo.moments_lock:
        files = [
            p for p in moments_dir.iterdir()
            if not p.name.startswith(".") and p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
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
    with tapo.moments_lock:
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
_status_futures: dict[str, tuple[int, Future]] = {}


def _status_snapshot(snapshot: dict, generated_at: float) -> dict:
    """Return a copy annotated with cache age without mutating the cache."""
    age = max(0.0, time.monotonic() - generated_at)
    result = dict(snapshot)
    result["drive_epoch"] = _drive.epoch
    result["_meta"] = {
        "age_ms": round(age * 1000),
        "stale": _status_invalidated or age >= _STATUS_CACHE_TTL_S,
    }
    return result


def _device_status_futures(generation: int) -> dict[str, tuple[int, Future]]:
    readers = {
        "lights": _lights_status,
        "vacuum": _vacuum_status,
        "purifier": _purifier_status,
    }
    with _status_futures_lock:
        for key, reader in readers.items():
            previous = _status_futures.get(key)
            if previous is None or previous[1].done():
                _status_futures[key] = generation, _STATUS_POOL.submit(_safe, reader)
        return dict(_status_futures)


def _build_status(controller) -> dict:
    global _last_status, _last_status_time, _status_invalidated
    requested_at = time.monotonic()
    with _status_refresh_lock:
        # A concurrent builder may already have produced the snapshot this
        # caller needed; reuse it instead of immediately polling again.
        with _last_status_lock:
            generation = _status_generation
            if not _status_invalidated and _last_status is not None and _last_status_time >= requested_at:
                return _status_snapshot(_last_status, _last_status_time)

        futures = _device_status_futures(generation)
        done, _ = wait([future for _, future in futures.values()], timeout=_STATUS_TIMEOUT_S)
        superseded = False
        result: dict = {}
        for key, (read_generation, future) in futures.items():
            if future in done and read_generation != generation:
                # A previous HTTP deadline expired while this reader was still
                # running. Its pre-action result cannot make the cache fresh.
                superseded = True
                result[key] = {"available": False, "error": "refresh in progress"}
                continue
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
            for key in futures:
                if result[key].get("available") and result[key].get("ok") is not False:
                    _device_success[key] = time.time()
            _last_status = result
            _last_status_time = generated_at
            _status_invalidated = superseded or generation != _status_generation
            if _status_invalidated:
                _status_dirty.set()
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
            if _status_invalidated or (now - _last_status_time) >= _STATUS_CACHE_TTL_S:
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
    _reject_unknown(body, {"text", "level"})
    text = _string_field(body, "text")
    level = _string_field(body, "level", required=False)
    if level is not None and level not in {"info", "alert"}:
        raise _ApiError("'level' must be 'info' or 'alert'.")
    try:
        return panel.send_alert(text, level or "info")
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


def _readiness(controller):
    workers = controller.diagnostics()
    with _last_status_lock:
        devices = {key: {"last_success": _device_success.get(key),
                         "available": (_last_status or {}).get(key, {}).get("available", False)
                         and (_last_status or {}).get(key, {}).get("ok") is not False}
                   for key in ("lights", "vacuum", "purifier")}
    with _sse_lock:
        events = {"clients": len(_sse_clients), "coalesced": _sse_coalesced, "rejected": _sse_rejected}
    locations = {"moments": Path(tapo.MOMENTS_DIR)}
    if getattr(controller, "household", None):
        locations["state"] = controller.household.path
    storage = {}
    for name, location in locations.items():
        path = location if location.is_dir() else location.parent
        try:
            free = shutil.disk_usage(path).free
            writable = os.access(path, os.W_OK | os.X_OK)
            if name == "state":
                writable = writable and location.is_file() and os.access(location, os.W_OK)
            elif location.exists():
                writable = writable and location.is_dir()
            storage[name] = {"ok": writable and free >= 10 * 1024 * 1024, "free_bytes": free}
        except OSError:
            storage[name] = {"ok": False, "free_bytes": None}
    ready = (all(s["ok"] for s in storage.values())
             and all(w["healthy"] and w["queue_depth"] < w["capacity"] for w in workers.values()))
    return {"ok": ready, "workers": workers, "devices": devices, "events": events, "storage": storage}


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
        "actions": [{"device": a.device, "action": a.name, "fields": a.fields} for a in ACTIONS.values()],
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


def _reject_unknown(body: dict, allowed: set[str]) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise _ApiError(f"Unknown field(s): {', '.join(unknown)}.")


def _string_field(body: dict, key: str, *, required: bool = True) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _ApiError(f"'{key}' must be a non-empty string.")
    return value.strip()


def _bool_field(body: dict, key: str, *, required: bool = True) -> bool | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, bool):
        raise _ApiError(f"'{key}' must be a JSON boolean.")
    return value


def _int_field(
    body: dict,
    key: str,
    *,
    required: bool = True,
    minimum: int | None = None,
    maximum: int | None = None,
    allowed: set[int] | None = None,
) -> int | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ApiError(f"'{key}' must be an integer.")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise _ApiError(f"'{key}' is outside the allowed range.")
    if allowed is not None and value not in allowed:
        raise _ApiError(f"'{key}' is not an allowed value.")
    return value


def _lights_action(controller, body: dict) -> dict:
    descriptor = _describe("lights", body)
    controller.apply_light(descriptor, body)
    return {"ok": True, "state": controller.state()}


def _describe(device, body):
    try:
        return describe(device, body)
    except ValueError as exc:
        raise _ApiError(str(exc)) from exc


def _lights_control(controller, body: dict) -> dict:
    _reject_unknown(body, {"targets", "power", "brightness", "color"})
    targets = body.get("targets", "all")
    if not isinstance(targets, str) and not (
        isinstance(targets, list)
        and all(isinstance(target, str) and target.strip() for target in targets)
    ):
        raise _ApiError("'targets' must be a string or a list of non-empty strings.")
    power = _bool_field(body, "power", required=False)
    brightness = _int_field(
        body, "brightness", required=False, minimum=1, maximum=100,
    )
    color = _string_field(body, "color", required=False)
    if power is None and brightness is None and color is None:
        raise _ApiError("Provide at least one of power, brightness, color.")
    result = controller.control_lights(
        targets,
        power=power,
        brightness=brightness,
        color=color,
    )
    return {"ok": bool(result.get("ok")), "result": result, "state": controller.state()}


def _vacuum_action(body: dict) -> dict:
    action = _string_field(body, "action")
    if action == "remote":
        _reject_unknown(body, {"action", "value", "session", "seq", "epoch"})
        descriptor = None
    else:
        descriptor = _describe("vacuum", body)
    try:
        with _vacuum_action_lock:
            if descriptor is None:
                return _drive.command(body)
            if action in {"sweep", "mop", "sweep_mop", "stop", "pause", "dock", "room_sweep"}:
                _drive.revoke()
            getattr(vacuum, descriptor.handler)(*descriptor.parameters(body))
    except _ApiError:
        raise
    except Exception as e:
        vacuum.reset_device()
        raise _ApiError(f"Vacuum command failed: {e}", status=502) from e
    notify_status_changed()
    return {"ok": True}


def _purifier_action(body: dict) -> dict:
    descriptor = _describe("purifier", body)
    try:
        getattr(purifier, descriptor.handler)(*descriptor.parameters(body))
    except _ApiError:
        raise
    except Exception as e:
        purifier.reset_device()
        raise _ApiError(f"Purifier command failed: {e}", status=502) from e
    notify_status_changed()
    return {"ok": True}


def _camera_capture(controller, body: dict) -> dict:
    _reject_unknown(body, {"flash"})
    flash = _bool_field(body, "flash", required=False)
    if flash is None:
        flash = False
    try:
        captured = controller.capture(flash=flash)
    except (ActionCancelledError, ActionInProgressError, ActionQueueFullError):
        raise
    except Exception as e:
        raise _ApiError(f"Capture failed: {e}", status=502) from e
    image_path = Path(captured)
    if not image_path.is_file():
        raise _ApiError("Capture produced no image.", status=502)
    notify_status_changed()
    return {"ok": True, "image": f"/moments/{image_path.name}"}


def _camera_delete(body: dict) -> dict:
    """Deletes a single captured image by its `/moments/<name>` URL or name."""
    _reject_unknown(body, {"image", "password"})
    if _DELETE_PASSWORD:
        supplied = _string_field(body, "password", required=False) or ""
        if not hmac.compare_digest(supplied, _DELETE_PASSWORD):
            raise _ApiError("Wrong or missing delete password.", status=403)
    raw = _string_field(body, "image")
    name = raw.rsplit("/", 1)[-1]  # accept a full /moments/<name> URL or bare name
    _delete_moment(name)
    notify_status_changed()
    return {"ok": True, **_moments_summary()}


def _chat(body: dict) -> dict:
    _reject_unknown(body, {"message"})
    message = _string_field(body, "message")
    if not _chat_available():
        return {"ok": False, "error": "Assistant is unavailable (LLM model not installed)."}
    try:
        reply = _run_chat(str(message))
    except Exception as e:
        raise _ApiError(f"Assistant error: {e}", status=502) from e
    return {"ok": True, "reply": reply}


# -- HTTP handler -------------------------------------------------------------

@lru_cache(maxsize=32)
def _asset(path: Path, fingerprint: tuple, compressed: bool):
    data = path.read_bytes()
    if compressed:
        data = gzip.compress(data, compresslevel=6, mtime=0)
    return data, '"' + hashlib.sha256(data).hexdigest() + '"'


def _accepts_gzip(header):
    preferences = {}
    for item in header.lower().split(","):
        encoding, *params = item.strip().split(";")
        weight = 1.0
        for param in params:
            if param.strip().startswith("q="):
                try:
                    weight = float(param.strip()[2:])
                except ValueError:
                    weight = 0.0
        preferences[encoding.strip()] = weight
    return preferences.get("gzip", preferences.get("*", 0)) > 0


def _credentials():
    return {name: tuple(secret.encode() for suffix in ("", "_PREVIOUS")
                        if (secret := os.getenv(f"COUKAB_{name.upper()}_PASSWORD{suffix}")))
            for name in ("browser", "panel")}


class _Handler(BaseHTTPRequestHandler):
    server_version = "Coukab/1.0"
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_CONNECTION_TIMEOUT_S)

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
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.close_connection = True
            raise _ApiError("Content-Length is required.", status=411)
        try:
            length = int(raw_length)
        except ValueError as exc:
            self.close_connection = True
            raise _ApiError("Invalid Content-Length.", status=400) from exc
        if length < 0:
            self.close_connection = True
            raise _ApiError("Invalid Content-Length.", status=400)
        if length > _MAX_JSON_BODY_BYTES:
            self.close_connection = True
            raise _ApiError("Request body is too large.", status=413)
        if not length:
            return {}
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            self.close_connection = True
            raise _ApiError("Content-Type must be application/json.", status=415)
        try:
            raw = self.rfile.read(length)
        except socket.timeout as exc:
            self.close_connection = True
            raise _ApiError("Request body timed out.", status=408) from exc
        if len(raw) != length:
            self.close_connection = True
            raise _ApiError("Incomplete request body.", status=400)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as e:
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
        stat = path.stat()
        compressible = path.suffix in {".html", ".css", ".js", ".svg", ".json", ".webmanifest"}
        compressed = compressible and _accepts_gzip(self.headers.get("Accept-Encoding", ""))
        data, etag = _asset(path, (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns), compressed)
        tags = [tag.strip().removeprefix("W/") for tag in self.headers.get("If-None-Match", "").split(",")]
        current = etag in tags or "*" in tags
        self.send_response(304 if current else 200)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if not current:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not current:
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
        except (BrokenPipeError, ConnectionResetError):
            return  # client navigated away mid-reply
        except Exception:
            failure = self._public_failure("Assistant failed.")
            try:
                write({"type": "error", "text": failure["error"], "error_id": failure["error_id"]})
            except OSError:
                pass

    def _serve_events(self) -> None:
        """Long-lived SSE stream: pushes status snapshots + heartbeats."""
        # The firmware tags its request so we can learn the panel's LAN IP.
        if self.headers.get("X-Coukab-Panel"):
            panel.register_panel(self.client_address[0])
        client = _EventMailbox()
        with _sse_lock:
            admitted = len(_sse_clients) < max(1, _MAX_HANDLER_THREADS // 2)
            if admitted:
                _sse_clients.add(client)
                latest = panel.get_alert()
                if latest.get("id"):
                    client.put("alert", json.dumps({"id": latest["id"]}))
            else:
                global _sse_rejected
                _sse_rejected += 1
        if not admitted:
            self.close_connection = True
            self._send_json({"ok": False, "error": "Event streams are busy."}, status=503)
            return
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

            while not self.server.stopped.is_set():
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
        with tapo.moments_lock:
            path = _resolve_moment(filename, thumb=thumb)
            content_type = _STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream")
            data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Timestamped file names are immutable, so allow caching.
        self.send_header("Cache-Control", "private, no-store" if any(self.server.credentials.values()) else "private, max-age=86400")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _authorize(self):
        if self.server.stopped.is_set():
            self.close_connection = True
            self._send_json({"error": "Service is stopping."}, status=503)
            return False
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin:
            allowed = {value.strip() for value in os.getenv("COUKAB_ALLOWED_ORIGINS", "").split(",") if value.strip()}
            if not allowed:
                allowed = {f"http://{self.headers.get('Host', '')}"}
            if origin not in allowed:
                self.close_connection = True
                self._send_json({"error": "Origin is not allowed."}, status=403)
                return False
        credentials = self.server.credentials
        if not any(credentials.values()):
            return True
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "basic":
            try:
                user, _, password = base64.b64decode(token, validate=True).partition(b":")
                if any(hmac.compare_digest(password, secret)
                       for secret in credentials.get(user.decode("ascii"), ())):
                    return True
            except (ValueError, UnicodeError, binascii.Error):
                pass
        self.close_connection = True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Coukab", charset="UTF-8"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        return False

    # -- routing --
    def do_GET(self) -> None:  # noqa: N802 - base API
        if not self._authorize():
            return
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
            elif path == "/api/history":
                self._send_json(self.server.household.history())
            elif path == "/api/routines":
                self._send_json({"routines": self.server.household.routines(), "runs": self.server.household.recent_runs()})
            elif path == "/api/ready":
                report = _readiness(self._controller)
                self._send_json(report, status=200 if report["ok"] else 503)
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
                state = self.server.receipts.state(job_id) or self._controller.job_state(job_id)
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
        except _ApiError as exc:
            self._send_api_error(exc)
        except Exception:
            self._send_json(self._public_failure("Internal server error."), status=500)

    def _public_failure(self, message):
        error_id = uuid.uuid4().hex[:12]
        logger.exception("%s %s failed [%s]", self.command, self.path.split("?")[0], error_id)
        return {"ok": False, "error": message, "error_id": error_id}

    def _send_api_error(self, exc):
        payload = (self._public_failure("Upstream service failed.") if exc.status >= 500
                   else {"ok": False, "error": str(exc)})
        self._send_json(payload, status=exc.status)

    def do_POST(self) -> None:  # noqa: N802 - base API
        if not self._authorize():
            return
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_body()
            if path == "/api/chat/stream":
                self._serve_chat_stream(body)
                return
            receipt = None
            key = self.headers.get("Idempotency-Key")
            if key is not None and path in _RETRIABLE:
                fingerprint = hashlib.sha256(json.dumps([path, body], sort_keys=True).encode()).digest()
                receipt, previous = self.server.receipts.reserve(key, fingerprint)
                if previous is not None:
                    status, payload = previous
                    self._send_json(payload, status=status)
                    return
            started = time.monotonic()
            status, payload = self._mutation_response(path, body)
            controller_job = payload.get("job_id") if status == 202 else None
            if receipt is not None:
                if controller_job:
                    status, payload = self.server.receipts.follow(receipt, self._controller, controller_job)
                else:
                    self.server.receipts.finish(receipt, (status, payload))
            if self.server.household and path in _RETRIABLE:
                label = path.removeprefix("/api/")
                device = label.split("/")[0]
                action = body.get("action")
                if isinstance(action, str) and (device, action) in ACTIONS:
                    label = f"{device}/{action}"
                outcome = "accepted" if status == 202 else "done" if status < 400 and payload.get("ok") and payload.get("complete", True) else "failed"
                store = self.server.household
                store.record_safely("http", label, outcome, time.monotonic() - started)
                if controller_job:
                    def finished(job):
                        store.record_safely("http", label, job["status"], time.monotonic() - started)
                    self._controller.on_job_finished(controller_job, finished)
        except ReceiptError as exc:
            status, payload = exc.status, {"ok": False, "error": str(exc)}
        except _ApiError as exc:
            self._send_api_error(exc)
            return
        except Exception:
            status, payload = 500, self._public_failure("Internal server error.")
        self._send_json(payload, status=status)

    def _mutation_response(self, path, body):
        if self.server.stopped.is_set():
            return 503, {"ok": False, "error": "Service is stopping."}
        try:
            if path in {"/api/routines/save", "/api/routines/delete", "/api/routines/run"}:
                try:
                    if path.endswith("/save"):
                        _reject_unknown(body, {"name", "steps", "schedule"})
                        self.server.household.save(body)
                        return 200, {"ok": True}
                    _reject_unknown(body, {"name"})
                    name = _string_field(body, "name")
                    if path.endswith("/delete"):
                        self.server.household.delete(name)
                        return 200, {"ok": True}
                    result = self._controller.run_action(self.server.run_routine, name, lane=self.server.routine_worker,
                                                         retry=False, publish_result=True, timeout=2)
                    return 200, result
                except ValueError as exc:
                    raise _ApiError(str(exc)) from exc
            if path == "/api/lights/action":
                result = _lights_action(self._controller, body)
            elif path == "/api/lights/control":
                result = _lights_control(self._controller, body)
            elif path == "/api/vacuum/action":
                result = _vacuum_action(body)
            elif path == "/api/purifier/action":
                result = _purifier_action(body)
            elif path == "/api/camera/capture":
                result = _camera_capture(self._controller, body)
            elif path == "/api/camera/delete":
                result = _camera_delete(body)
            elif path == "/api/panel/alert":
                result = _panel_alert_send(body)
            elif path == "/api/chat":
                result = _chat(body)
            else:
                return 404, {"error": "Not found."}
            return 200, result
        except _ApiError as exc:
            return exc.status, (self._public_failure("Upstream service failed.") if exc.status >= 500
                                else {"ok": False, "error": str(exc)})
        except ActionQueueFullError:
            return 429, {"ok": False, "error": "Too many pending actions."}
        except ActionInProgressError as exc:
            return accepted(exc.job_id)
        except TimeoutError:
            return 504, self._public_failure("Action timed out.")
        except yeelight.BulbBatchError as exc:
            payload = self._public_failure("One or more bulbs failed.")
            payload["failed_bulbs"] = sorted(exc.failures)
            return 502, payload
        except Exception:
            return 500, self._public_failure("Internal server error.")


_RETRIABLE = {"/api/lights/action", "/api/lights/control", "/api/purifier/action",
              "/api/vacuum/action", "/api/camera/capture", "/api/camera/delete", "/api/routines/run"}


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server with explicit admission control."""

    request_queue_size = _MAX_HANDLER_THREADS

    def __init__(self, *args, **kwargs):
        self.household = None
        self.scheduler = None
        self.sampler = None
        self.receipts = Receipts()
        self.stopped = threading.Event()
        self.notifier = None
        self.credentials = _credentials()
        self._handler_slots = threading.BoundedSemaphore(_MAX_HANDLER_THREADS)
        super().__init__(*args, **kwargs)
        self.routine_worker = Worker("household-routines", 32)

    def run_routine(self, name, local_time=None):
        def execute(step):
            if self.stopped.is_set():
                raise RuntimeError("Controller stopping")
            device = step["device"]
            body = {k: v for k, v in step.items() if k != "device"}
            started = time.monotonic()
            outcome = "failed"
            try:
                if device == "lights":
                    try:
                        _lights_action(self.controller, body)
                    except ActionInProgressError as exc:
                        while not self.stopped.wait(0.25):
                            job = self.controller.job_state(exc.job_id)
                            if not job or job["status"] in {"cancelled", "failed"}:
                                raise RuntimeError("Light action failed")
                            if job["status"] == "done":
                                break
                        else:
                            raise RuntimeError("Controller stopping")
                elif device == "purifier":
                    _purifier_action(body)
                elif device == "vacuum":
                    _vacuum_action(body)
                outcome = "done"
            finally:
                self.household.record_safely("schedule" if local_time else "routine",
                                             f"{device}/{body['action']}", outcome, time.monotonic() - started)
        return self.household.run(name, execute, local_time=local_time)

    def schedule_loop(self):
        previous = None
        while not self.stopped.is_set():
            now = datetime.now().astimezone()
            minute = int(now.timestamp() // 60)
            try:
                if minute != previous:
                    for routine in self.household.routines():
                        if routine["schedule"] == now.strftime("%H:%M"):
                            self.routine_worker.submit(partial(self.run_routine, routine["name"], now))
                    previous = minute
            except Exception:
                logger.exception("Could not queue scheduled routines")
            self.stopped.wait(1)

    def sample_loop(self):
        while not self.stopped.is_set():
            try:
                snapshot = _cached_status(self.controller)
                self.household.sample(snapshot.get("purifier", {}), time.time())
            except Exception:
                logger.exception("Could not record environmental sample")
            self.stopped.wait(60)

    def shutdown(self):
        self.stopped.set()
        _status_dirty.set()
        self.routine_worker.stop()
        super().shutdown()
        if self.notifier:
            drive_stop = threading.Thread(target=_drive.stop_on_shutdown, name="drive-stop", daemon=True)
            drive_stop.start()
            # Wake existing streams so their handlers can release their sockets.
            _sse_broadcast("close", "{}")
            self.controller.on_action = None
            panel.set_broadcaster(None)
            for thread in (self.notifier, self.scheduler, self.sampler, self.routine_worker.thread, drive_stop):
                thread.join(timeout=0.25)

    def process_request(self, request, client_address) -> None:
        if not self._handler_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


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
    httpd = _BoundedThreadingHTTPServer((host, port), _Handler)
    httpd.controller = controller  # type: ignore[attr-defined]
    httpd.daemon_threads = True

    httpd.household = getattr(controller, "household", None) or Household(os.getenv("COUKAB_STATE_DB", "coukab.sqlite3"))
    httpd.household.recover()
    httpd.scheduler = threading.Thread(target=httpd.schedule_loop, name="scheduler", daemon=True)
    httpd.sampler = threading.Thread(target=httpd.sample_loop, name="environment-sampler", daemon=True)
    httpd.scheduler.start()
    httpd.sampler.start()

    # Live updates: every completed controller job (keypad or web) triggers a
    # debounced status push to all SSE subscribers.
    _controller_ref = controller
    controller.on_action = notify_status_changed
    panel.set_broadcaster(_sse_broadcast)  # let the panel module push alerts
    httpd.notifier = threading.Thread(target=_notifier_loop, args=(httpd.stopped,), name="sse-notifier", daemon=True)
    httpd.notifier.start()

    threading.Thread(target=httpd.serve_forever, name="web-server", daemon=True).start()
    logger.info(
        "Web interface: http://%s:%d  (open http://%s:%d on your phone).",
        host, port, lan_ip(), port,
    )
    return httpd
