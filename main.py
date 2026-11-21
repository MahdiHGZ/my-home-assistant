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
  Running main.py also starts a LAN web UI (default http://0.0.0.0:8080)
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

from device_actions import LightActions
from household import Household
import argparse
import functools
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from worker import Worker
from pathlib import Path
from typing import Callable

from action_errors import ActionCancelledError, ActionInProgressError, ActionQueueFullError

try:
    import evdev
except ModuleNotFoundError:  # web/controller tests and web-only hosts need no evdev
    evdev = None  # type: ignore[assignment]

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
_ACTION_QUEUE_SIZE = 5
_JOB_HISTORY_SIZE = 100


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


# Each binding carries the action label used by the dashboard's printable map.
_SINGLE_BINDINGS = {
    "KEY_KP1": ("key_vacuum_toggle", "_do_vacuum_toggle", ()),
    "KEY_KP4": ("party", "_do_party_toggle", ()),
    "KEY_KP5": ("cycle", "_do_color_cycle_start", ()),
    "KEY_KPPLUS": ("key_bright_up", "_do_brightness", (_BRIGHTNESS_STEP,)),
    "KEY_KPMINUS": ("key_bright_down", "_do_brightness", (-_BRIGHTNESS_STEP,)),
}
_COMBO_BINDINGS = {
    "KEY_KP4": ("prev", "_do_color_cycle_advance", (-1,)),
    "KEY_KP6": ("next", "_do_color_cycle_advance", (1,)),
    "KEY_KPMINUS": ("all_off", "_do_all_off", ()),
    "KEY_KPASTERISK": ("random", "_do_random_color", (False,)),
    "KEY_KPSLASH": ("each", "_do_random_color", (True,)),
    "KEY_BACKSPACE": ("undo", "_do_undo", ()),
}
_CAPTURE_BINDINGS = {
    "KEY_KP1": ("capture", False, False),
    "KEY_KP2": ("capture_flash", True, False),
    "KEY_KP3": ("key_countdown", True, True),
}
_SINGLE_KEYS = _SINGLE_KEY_MODES.keys() | _SINGLE_BINDINGS.keys()
_COMBO_KEYS = _COMBO_KEY_MODES.keys() | _COMBO_BINDINGS.keys() | _CAPTURE_BINDINGS.keys()


def keypad_bindings():
    return [
        {"code": key.removeprefix("KEY_"), "combo": combo, "mode": mode.name}
        for combo, modes in ((False, _SINGLE_KEY_MODES), (True, _COMBO_KEY_MODES))
        for key, mode in modes.items()
    ] + [
        {"code": key.removeprefix("KEY_"), "combo": combo, "label": binding[0]}
        for combo, bindings in ((False, _SINGLE_BINDINGS), (True, _COMBO_BINDINGS), (True, _CAPTURE_BINDINGS))
        for key, binding in bindings.items()
    ]


# ---------------------------------------------------------------------------
# Camera capture helpers
# ---------------------------------------------------------------------------

def _capture(bulbs: dict[str, str], *, flash: bool, cap=None, countdown=False) -> Path:
    """Captures a photo, optionally flashing full brightness first.

    The camera is connected before any lighting changes, so a slow or
    failed RTSP connect never leaves the room stuck in flash lighting.
    """
    own_connection = cap is None
    if own_connection:
        cap = tapo_camera_utils.connect()
    if cap is None:
        raise tapo_camera_utils.TapoError("Camera connection failed.")

    snapshot = None
    lighting_changed = False
    try:
        if flash:
            tapo_camera_utils.require_capture_allowed()
            snapshot = yeelight_bulb_utils.read_state(bulbs)
            if countdown:
                tapo_camera_utils.require_capture_allowed()
                lighting_changed = True
                yeelight_bulb_utils.run_row_pair_sweep_count(bulbs)
            tapo_camera_utils.require_capture_allowed()
            lighting_changed = True
            _prepare_flash_lighting(bulbs)
        image_path = tapo_camera_utils.capture_moment(cap)
        if image_path is None:
            raise tapo_camera_utils.TapoError("Failed to capture image frame.")
        return image_path
    finally:
        if own_connection:
            cap.release()
        if lighting_changed and snapshot is not None:
            yeelight_bulb_utils.apply_state(bulbs, snapshot)


def _prepare_flash_lighting(bulbs: dict[str, str]) -> None:
    """Creates a quick blink before full-on flash lighting."""
    yeelight_bulb_utils.full_off(bulbs)
    time.sleep(_FLASH_BLINK_OFF_S)
    tapo_camera_utils.require_capture_allowed()
    # Transient capture lighting — don't save it as the bulbs' power-on
    # default; the real state is restored (and persisted) afterward.
    yeelight_bulb_utils.apply_mode(bulbs, MODES["full_on"], persist=False)
    time.sleep(_CAPTURE_SETTLE_S)


def retry_with_discovery(max_attempts=3):
    """Retries bulb actions after rediscovering bulb IPs.

    The worker records and logs failures without stopping the keypad event loop.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except yeelight_bulb_utils.BrightnessWriteError:
                    raise
                except Exception:
                    logger.exception(
                        "%s failed (attempt %d/%d).", fn.__name__, attempt, max_attempts
                    )
                    if attempt == max_attempts:
                        raise
                    yeelight_bulb_utils.update_discover_config()
                    time.sleep(1)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------

class Controller(LightActions):
    """Serialized action dispatcher shared by the keypad and the web UI.

    Lighting changes share one worker. A second bounded worker prepares
    camera streams and submits flash, exposure and restoration as one
    lighting job. Both workers keep device I/O off the evdev read loop.

    The ``_do_*`` methods hold the actual device logic and are reused by
    both the keypad handlers and :meth:`run_action` (the web entry point).
    """

    keypad_bindings = staticmethod(keypad_bindings)

    def __init__(self, household=None) -> None:
        self.household = household
        self._color_cycle_index = 0
        # Last applied scene mode name (e.g. "movie"); None after all-off.
        # Surfaced via state() so remote panels can highlight the active mode.
        self._last_mode: str | None = None
        self.lights_worker = Worker("key-worker", _ACTION_QUEUE_SIZE)
        self.camera_worker = Worker("camera-worker", 2)
        self._queue = self.lights_worker.queue
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, dict[str, object]] = {}
        self._job_listeners = {}
        # Called after every completed job (keypad or web). The web server
        # hooks this to push live status updates to connected browsers.
        self.on_action: Callable[[], None] | None = None


    # -- queue plumbing -----------------------------------------------------

    def dispatch(self, keycode: str, *, combo: bool) -> None:
        """Queues a keypad action; returns immediately."""
        if not isinstance(keycode, str) or keycode not in (_COMBO_KEYS if combo else _SINGLE_KEYS):
            return
        handler = self._handle_combo_key if combo else self._handle_single_key
        lane = self.lights_worker
        if combo and keycode in _CAPTURE_BINDINGS:
            lane = self.camera_worker
            _, flash, countdown = _CAPTURE_BINDINGS[keycode]
            fn = functools.partial(self._prepare_capture, flash=flash, countdown=countdown)
        else:
            fn = functools.partial(handler, keycode)

        def run():
            started = time.monotonic()
            outcome = "failed"
            try:
                fn()
                outcome = "done"
            finally:
                if self.household:
                    label = ("combo:" if combo else "") + keycode
                    self.household.record_safely("keypad", label, outcome, time.monotonic() - started)
                if self.on_action:
                    self.on_action()
        try:
            lane.submit(run)
        except ActionQueueFullError:
            logger.warning("Controller busy; dropping keypad action %s.", keycode)

    def diagnostics(self):
        return {"actions": self.lights_worker.diagnostics(), "camera": self.camera_worker.diagnostics()}

    def stop_accepting(self):
        for lane in (self.camera_worker, self.lights_worker):
            lane.stop()

    def shutdown(self, timeout=5):
        deadline = time.monotonic() + timeout
        self.stop_accepting()
        for lane in (self.camera_worker, self.lights_worker):
            lane.thread.join(max(0, deadline - time.monotonic()))
        yeelight_bulb_utils.stop_party_dance()

    QueueFullError = ActionQueueFullError  # compatibility for existing callers

    def _record_job(self, job_id: str, **fields: object) -> None:
        with self._jobs_lock:
            if job_id not in self._jobs:
                # Retain a bounded amount of diagnostic history, but never
                # evict an active job just to make room.
                if len(self._jobs) >= _JOB_HISTORY_SIZE:
                    for old_id, info in list(self._jobs.items()):
                        if info.get("status") in {"done", "failed", "cancelled"}:
                            self._jobs.pop(old_id, None)
                            break
                self._jobs[job_id] = {"id": job_id}
            state = self._jobs[job_id]
            state.update(fields)
            listeners = self._job_listeners.pop(job_id, []) if state.get("status") in {"done", "failed", "cancelled"} else []
            snapshot = dict(state)
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                logger.exception("Job completion listener failed")

    def on_job_finished(self, job_id, callback):
        """Attach atomically even when the worker has just completed."""
        with self._jobs_lock:
            state = self._jobs.get(job_id)
            if state and state["status"] in {"queued", "running"}:
                self._job_listeners.setdefault(job_id, []).append(callback)
                return
            snapshot = dict(state) if state else {"status": "failed", "error": "Action receipt expired."}
        callback(snapshot)

    def job_state(self, job_id: str) -> dict[str, object] | None:
        """Return a copy of an asynchronous action's observable state."""
        with self._jobs_lock:
            state = self._jobs.get(job_id)
            return dict(state) if state is not None else None

    def run_action(self, fn: Callable[..., object], *args, timeout: float | None = 60.0,
                   lane=None, retry=True, publish_result=False, **kwargs):
        """Runs an action on the worker thread and waits for its result.

        Used by the web interface so its actions are serialized with keypad
        presses and benefit from the same bulb-rediscovery retry. The result
        is returned to the caller, and any error is re-raised (unlike the
        keypad path, which only logs) so the HTTP layer can report it.
        """
        job_id = uuid.uuid4().hex
        box: dict[str, object] = {}
        done = threading.Event()
        state_lock = threading.Lock()
        phase = "queued"
        self._record_job(
            job_id,
            action=getattr(fn, "__name__", str(fn)),
            status=phase,
            created_at=time.time(),
        )

        def job() -> None:
            nonlocal phase
            with state_lock:
                if phase == "cancelled":
                    done.set()
                    return
                phase = "running"
            self._record_job(job_id, status="running", started_at=time.time())
            try:
                box["result"] = (self._with_discovery_retry(fn, *args, **kwargs) if retry
                                 else fn(*args, **kwargs))
            except BaseException as e:  # noqa: BLE001 — relayed to the HTTP caller
                box["error"] = e
            finally:
                with state_lock:
                    phase = "failed" if "error" in box else "done"
                update: dict[str, object] = {"status": phase, "finished_at": time.time()}
                if "error" in box:
                    logger.error("Action %s failed: %s", job_id, box["error"])
                    update["error"] = "Device action failed."
                if phase == "done" and publish_result:
                    update["result"] = box["result"]
                if phase == "done" and isinstance(box.get("result"), Path):
                    update["result"] = {"ok": True, "image": f"/moments/{box['result'].name}"}
                self._record_job(job_id, **update)
                done.set()
                if self.on_action:
                    self.on_action()

        def cancel():
            nonlocal phase
            with state_lock:
                phase = "cancelled"
                box["error"] = ActionCancelledError(job_id)
                self._record_job(job_id, status=phase, error="Controller stopped.", finished_at=time.time())
                done.set()

        try:
            (lane or self.lights_worker).submit(job, cancel)
        except ActionQueueFullError as e:
            self._record_job(job_id, status="cancelled", error="queue full", finished_at=time.time())
            raise ActionQueueFullError("Too many pending actions in the queue.") from e
        if not done.wait(timeout):
            with state_lock:
                if phase == "queued":
                    phase = "cancelled"
                    self._record_job(
                        job_id,
                        status="cancelled",
                        error="caller deadline expired before execution",
                        finished_at=time.time(),
                    )
                    raise ActionCancelledError(job_id)
                if phase == "running":
                    # Running socket I/O cannot be safely killed. Report an
                    # accepted job ID instead of telling the client it failed.
                    raise ActionInProgressError(job_id)
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box.get("result")

    @staticmethod
    def _with_discovery_retry(fn: Callable[..., object], *args, max_attempts: int = 2, **kwargs):
        """Calls ``fn``, rediscovering bulb IPs once before a final retry."""
        for attempt in range(1, max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except yeelight_bulb_utils.BrightnessWriteError:
                raise
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

    def _do_brightness(self, step: int, targets: list[str] | None = None) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if targets is not None:
            selected = {name: bulbs[name] for name in targets if name in bulbs}
            if not selected or len(selected) != len(set(targets)):
                raise ValueError("One or more selected bulbs are no longer registered.")
            bulbs = selected
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("Brightness step %+d.", step)
        snapshot = yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.adjust_brightness(bulbs, step, snapshot=snapshot)

    def _do_all_off(self) -> None:
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        logger.info("All bulbs off.")
        yeelight_bulb_utils.save_state(bulbs)
        yeelight_bulb_utils.full_off(bulbs)
        self._last_mode = None

    def _do_random_color(self, each: bool) -> None:
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
        targets: str | list[str] | None,
        *,
        power: bool | None = None,
        brightness: int | None = None,
        color: str | None = None,
        before_control=None,
    ) -> dict[str, object]:
        """Web-only direct control: power, brightness and arbitrary colors.

        Stops the party dance first (it would otherwise overwrite the change)
        but does not push an undo snapshot, so dragging a slider doesn't flood
        the undo stack.
        """
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        if before_control is not None:
            before_control()
        if yeelight_bulb_utils.is_party_running() and (
            power is not None or brightness is not None or color is not None
        ):
            yeelight_bulb_utils.stop_party_dance()
        result = yeelight_bulb_utils.control_bulbs(
            targets,
            power=power,
            brightness=brightness,
            color=color,
            registered=bulbs,
        )
        if not result.get("ok"):
            entries = result.get("bulbs", [])
            failures = {
                str(entry.get("name", "unknown")): RuntimeError(
                    str(entry.get("error", "Unknown bulb error."))
                )
                for entry in entries
                if isinstance(entry, dict) and not entry.get("ok")
            }
            raise yeelight_bulb_utils.BulbBatchError(failures, result=result)
        return result

    def capture(self, *, flash: bool):
        if flash:
            return self.run_action(self._prepare_capture, flash=True, lane=self.camera_worker,
                                   retry=False, timeout=90)
        return self.run_action(self._plain_capture, lane=self.camera_worker, retry=False, timeout=90)

    @staticmethod
    def _plain_capture():
        captured = tapo_camera_utils.capture_moment_for_model(include_data_uri=False)
        return Path(captured["image_path"])

    def _prepare_capture(self, *, flash: bool, countdown=False):
        tapo_camera_utils.require_capture_allowed()
        config = tapo_camera_utils.credentials()
        client = tapo_camera_utils.get_client(config)
        cap = tapo_camera_utils.connect(config=config, client=client)
        if cap is None:
            raise tapo_camera_utils.TapoError("Camera connection failed.")
        try:
            # The camera lane retains ownership until the exposure job has
            # completed or been cancelled, including during service shutdown.
            return self.run_action(self._expose, cap, client, flash=flash, countdown=countdown,
                                   retry=False, timeout=None)
        finally:
            cap.release()

    def _expose(self, cap, client, *, flash, countdown):
        bulbs = yeelight_bulb_utils.load_registered_bulbs()
        tapo_camera_utils.require_capture_allowed()
        tapo_camera_utils.ensure_camera_on(client)
        tapo_camera_utils.require_capture_allowed()
        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()
        return _capture(bulbs, flash=flash, cap=cap, countdown=countdown)

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
            raise

    # -- keypad dispatch ----------------------------------------------------

    @retry_with_discovery(max_attempts=3)
    def _handle_single_key(self, keycode: str) -> None:
        """Dispatches standalone (non-combo) key actions."""
        if keycode in _SINGLE_BINDINGS and _SINGLE_BINDINGS[keycode][1] == "_do_party_toggle":
            self._do_party_toggle()
            return

        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()

        if keycode in _SINGLE_KEY_MODES:
            self._do_apply_mode(_SINGLE_KEY_MODES[keycode])
        elif keycode in _SINGLE_BINDINGS:
            _, method, args = _SINGLE_BINDINGS[keycode]
            getattr(self, method)(*args)

    @retry_with_discovery(max_attempts=3)
    def _handle_combo_key(self, keycode: str) -> None:
        """Dispatches KP0 + key combo actions."""
        binding = _COMBO_BINDINGS.get(keycode)
        if binding and binding[1] == "_do_color_cycle_advance":
            self._do_color_cycle_advance(*binding[2])
            return

        if yeelight_bulb_utils.is_party_running():
            yeelight_bulb_utils.stop_party_dance()

        if keycode in _COMBO_KEY_MODES:
            self._do_apply_mode(_COMBO_KEY_MODES[keycode])
        elif binding:
            _, method, args = binding
            getattr(self, method)(*args)


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
    parser.add_argument("--demo", action="store_true", help="Serve simulated household devices on loopback.")
    parser.add_argument("--watch-assets", action="store_true", help="Refresh dashboard tabs after local asset edits.")
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
    if evdev is None:
        logger.error("The evdev package is unavailable; keypad input is disabled.")
        if required:
            raise SystemExit(1)
        return False
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
        device.close()
        logger.info("Stopped.")
    return True


def _systemd_notify(message):
    address = os.getenv("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1)
            sock.sendto(message.encode(), address.replace("@", "\0", 1) if address.startswith("@") else address)


def _watchdog(controller, stopped):
    period = int(os.getenv("WATCHDOG_USEC", "0")) / 1_000_000
    if period > 0:
        while not stopped.wait(period / 3):
            if all(lane["healthy"] for lane in controller.diagnostics().values()):
                _systemd_notify("WATCHDOG=1")


def _terminate(signum, frame):
    raise KeyboardInterrupt


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    signal.signal(signal.SIGTERM, _terminate)
    # A third-party device call can outlive Python executor shutdown. Bound
    # process exit even when a device never releases an in-flight socket.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(1))
    demo = None
    if args.demo:
        if args.no_web:
            raise SystemExit("--demo requires the web dashboard.")
        from demo_house import install
        demo = install()
        args.web_host = "127.0.0.1"
    controller = Controller(Household(os.getenv("COUKAB_STATE_DB", "coukab.sqlite3")))
    stopped = threading.Event()

    web = None
    if not args.no_web:
        import web_server
        try:
            web = web_server.start(controller, host=args.web_host, port=args.web_port,
                                   demo=args.demo, watch_assets=args.watch_assets)
        except OSError as e:
            logger.error("Could not start web interface on %s:%d — %s", args.web_host, args.web_port, e)

    try:
        _systemd_notify("READY=1")
        threading.Thread(target=_watchdog, args=(controller, stopped), daemon=True).start()
        ran = not args.demo and run_keyboard(args.device, controller, grab=args.grab, required=web is None)
        if not ran and web is not None:
            # Keypad unavailable but the web interface is up — stay alive for it.
            logger.info("Web-only mode. Press Ctrl+C to stop.")
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                logger.info("Interrupted.")
    except KeyboardInterrupt:
        pass
    finally:
        signal.alarm(15)
        stopped.set()
        _systemd_notify("STOPPING=1")
        controller.stop_accepting()
        if web is not None:
            web.shutdown()
            web.server_close()
        controller.shutdown()
        if demo:
            demo.cleanup()


if __name__ == "__main__":
    main()
