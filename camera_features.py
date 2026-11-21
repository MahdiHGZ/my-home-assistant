"""Optional camera jobs, gallery search and read-only capability discovery."""

from __future__ import annotations

import io
import logging
import tempfile
import threading
import time
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path

import camera_policy
import tapo_camera_utils as tapo

logger = logging.getLogger(__name__)
_MAX_FRAMES = 60
_MAX_JPEG_BYTES = 512 * 1024
_MAX_EVENTS = 40
_PRESENCE_INTERVAL = 10
_MAX_PRESENCE_GAP = 3 * _PRESENCE_INTERVAL


def jpeg(frame) -> bytes:
    cv2 = tapo.cv2
    height, width = frame.shape[:2]
    scale = min(1.0, 640 / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok or data.nbytes > _MAX_JPEG_BYTES:
        raise tapo.TapoError("Could not encode camera preview.")
    return data.tobytes()


def _fresh_frame():
    cap = tapo.connect(substream=True)
    if cap is None:
        raise tapo.TapoError("Could not connect to the camera.")
    try:
        frame = None
        for _ in range(3):
            frame = tapo.read_frame(cap)
            if frame is None:
                raise tapo.TapoError("Could not read a fresh camera frame.")
        return frame
    finally:
        cap.release()


def preview() -> bytes:
    data = jpeg(_fresh_frame())
    tapo.require_capture_allowed()
    return data


def gallery_search(paths: list[Path], query: str) -> list[dict]:
    results = []
    terms = query.casefold().split()
    with tapo.moments_lock:
        for path in paths:
            caption_path = tapo.moment_caption_path(path)
            caption = ""
            if caption_path.is_file() and not caption_path.is_symlink():
                try:
                    with caption_path.open(encoding="utf-8") as stored:
                        caption = stored.read(240)
                except (OSError, UnicodeError):
                    pass
            text = f"{path.name} {caption}".casefold()
            if path.is_file() and all(term in text for term in terms):
                results.append({"name": path.name, "caption": caption})
    return results


def set_caption(path: Path, caption: str) -> None:
    """Update one caption while the caller holds moments_lock."""
    destination = tapo.moment_caption_path(path)
    if not caption:
        destination.unlink(missing_ok=True)
        return
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".caption-", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(caption)
            output.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def daily_digest(paths: list[Path], history: dict, now: datetime | None = None) -> dict:
    date = (now or datetime.now()).date()
    with tapo.moments_lock:
        retained = [path for path in paths
                    if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime).date() == date]
    counts: dict[str, int] = {}
    for action in history["actions"]:
        if datetime.fromtimestamp(action["ts"]).date() == date:
            name = action["action"].partition("/")[0]
            counts[name] = counts.get(name, 0) + 1
    return {"date": date.isoformat(), "moments": gallery_search(retained, ""), "device_actions": counts}


def _motion_sample(frame):
    cv2 = tapo.cv2
    gray = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def _has_motion(previous, current) -> bool:
    if previous is None:
        return False
    cv2 = tapo.cv2
    _, mask = cv2.threshold(cv2.absdiff(previous, current), 30, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(mask) >= mask.size * 0.04


def _classify_presence(frame, sample, motion: bool) -> str:
    if min(frame.shape[:2]) < 64:
        return "unknown"
    try:
        people, faces = tapo._detect_people(frame, require_complete=True)
    except Exception:
        logger.debug("Camera presence detectors unavailable", exc_info=True)
        return "unknown"
    if people or faces:
        return "person"
    # Motion without a match, an under/overexposed image, and a nearly
    # uniform image cannot establish a negative observation.
    if motion or not 20 <= sample.mean() <= 235 or sample.std() < 4:
        return "unknown"
    return "not_detected"


def _motor_capable(client) -> bool:
    try:
        result = client.getMotorCapability()
        return (result["error_code"] == 0 and isinstance(result["motor"]["capability"], dict)
                and bool(result["motor"]["capability"]))
    except Exception:
        return False


_ptz_lock = threading.Lock()
_DIRECTIONS = {"right": 0, "up": 90, "left": 180, "down": 270}


def _ptz_client():
    tapo.require_capture_allowed()
    client = tapo.get_client()
    tapo.ensure_camera_on(client)
    if not _motor_capable(client):
        return None
    return client


def ptz_probe() -> dict:
    """Read capabilities only. Unknown responses cannot enable controls."""
    try:
        client = _ptz_client()
        if client is None:
            return {"supported": False}
        presets = client.getPresets()
        return {"supported": True, "presets": presets if isinstance(presets, dict) else {}}
    except Exception:
        return {"supported": False}


def move_camera(body: dict) -> None:
    if tuple(body) == ("direction",) and isinstance(body["direction"], str):
        if body["direction"] not in _DIRECTIONS:
            raise ValueError("Unknown camera movement direction.")
    elif tuple(body) != ("preset",) or not isinstance(body["preset"], str):
        raise ValueError("Select one movement direction or preset.")

    if not _ptz_lock.acquire(blocking=False):
        raise ValueError("Camera movement is already in progress.")
    try:
        client = _ptz_client()
        if client is None:
            raise ValueError("Camera movement is unsupported.")
        if "direction" in body:
            tapo.require_capture_allowed()
            client.moveMotorStep(_DIRECTIONS[body["direction"]])
        else:
            preset = body["preset"]
            if preset not in client.getPresets():
                raise ValueError("Unknown camera preset.")
            tapo.require_capture_allowed()
            client.setPreset(preset)
    finally:
        _ptz_lock.release()


class CameraJobs:
    def __init__(self, stopped: threading.Event, on_moment, on_presence=lambda rule: None):
        self.stopped = stopped
        self.on_moment = on_moment
        self.on_presence = on_presence
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=_MAX_EVENTS)
        self._previous = None
        self._last_event = float("-inf")
        self._next_frame = 0.0
        self._interval = 0
        self._missed = 0
        self._state = "idle"
        self._classification_ms = None
        self._presence_intervals: deque[dict] = deque(maxlen=_MAX_EVENTS)
        self._last_seen: str | None = None
        self._quiet_negatives = 0
        self._presence_tracking = False
        self._next_presence_check = 0.0
        self._last_frame_at: float | None = None
        self._sweep_cancel = threading.Event()
        self._sweep = {"state": "idle", "moments": []}

    def _record_presence(self, observed: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            if observed == "not_detected":
                self._quiet_negatives = min(2, self._quiet_negatives + 1)
                if self._quiet_negatives < 2:
                    observed = "unknown"
            else:
                self._quiet_negatives = 0
            if observed == "person":
                self._last_seen = timestamp
            if self._presence_intervals and self._presence_intervals[0]["state"] == observed:
                self._presence_intervals[0]["last_observed"] = timestamp
            else:
                self._presence_intervals.appendleft({
                    "state": observed, "started": timestamp, "last_observed": timestamp,
                })

    def _break_observation(self) -> None:
        self._previous = None
        self._last_frame_at = None
        self._presence_tracking = False
        self._next_presence_check = 0.0
        if self._presence_intervals:
            self._record_presence("unknown")

    def _observe_presence(self, frame, sample, motion: bool, capture_due: bool, now: float) -> bool:
        self._presence_tracking = self._presence_tracking or motion
        if not self._presence_tracking:
            return False
        if not capture_due and now < self._next_presence_check:
            return False
        self._next_presence_check = now + _PRESENCE_INTERVAL
        start = time.process_time()
        observed = _classify_presence(frame, sample, motion)
        elapsed = round(1000 * (time.process_time() - start), 1)
        with self._lock:
            self._classification_ms = elapsed
        self._record_presence(observed)
        return observed == "person"

    @staticmethod
    def _frames() -> Path:
        return tapo.MOMENTS_DIR / ".timelapse"

    def status(self, settings: dict) -> dict:
        with self._lock:
            return {
                "settings": settings,
                "private": camera_policy.is_private(settings),
                "events": [event for event in self._events
                           if (tapo.MOMENTS_DIR / event["moment"].rsplit("/", 1)[-1]).is_file()],
                "timelapse_frames": len(list(self._frames().glob("*.jpg"))),
                "timelapse_missed": self._missed,
                "state": self._state,
                "classification_ms": self._classification_ms,
                "presence": {
                    "last_seen": self._last_seen,
                    "intervals": [interval.copy() for interval in self._presence_intervals],
                },
                "sweep": {**self._sweep, "moments": list(self._sweep["moments"])},
            }

    def start_sweep(self, presets) -> None:
        if (not isinstance(presets, list) or not 2 <= len(presets) <= 4
                or any(not isinstance(x, str) or len(x) > 32 for x in presets)
                or len(set(presets)) != len(presets)):
            raise ValueError("Select two to four distinct camera presets.")
        tapo.require_capture_allowed()
        with self._lock:
            if self._sweep["state"] == "running":
                raise ValueError("Multi-angle capture already running.")
            self._sweep_cancel.clear()
            self._sweep = {"state": "running", "moments": []}
        threading.Thread(target=self._run_sweep, args=(presets,), name="camera-sweep", daemon=True).start()

    def cancel_sweep(self) -> None:
        self._sweep_cancel.set()

    def _run_sweep(self, presets: list[str]) -> None:
        state = "failed"
        try:
            if not _ptz_lock.acquire(blocking=False):
                return
            try:
                client = _ptz_client()
                if client is None:
                    return
                available = client.getPresets()
                if not all(preset in available for preset in presets):
                    return
                for preset in presets:
                    if self.stopped.is_set() or self._sweep_cancel.is_set():
                        state = "cancelled"
                        return
                    tapo.require_capture_allowed()
                    client.setPreset(preset)
                    if self._sweep_cancel.wait(2) or self.stopped.is_set():
                        state = "cancelled"
                        return
                    frame = _fresh_frame()
                    if self.stopped.is_set() or self._sweep_cancel.is_set():
                        state = "cancelled"
                        return
                    path = tapo.save_moment(frame)
                    with self._lock:
                        self._sweep["moments"].append(f"/moments/{path.name}")
                    self.on_moment()
                state = "done"
            finally:
                _ptz_lock.release()
        except tapo.CameraPrivateError:
            state = "cancelled"
        except Exception:
            logger.debug("Multi-angle capture failed", exc_info=True)
        finally:
            with self._lock:
                self._sweep["state"] = state

    def _write_timelapse(self, data: bytes) -> None:
        with camera_policy.lock, self._lock:
            tapo.require_capture_allowed()
            path = self._frames()
            if path.is_symlink():
                raise OSError("Invalid timelapse directory.")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            temporary = path / ".frame.tmp"
            try:
                temporary.write_bytes(data)
                tapo.require_capture_allowed()
                temporary.replace(path / f"{timestamp}.jpg")
            finally:
                temporary.unlink(missing_ok=True)
            for old in sorted(path.glob("*.jpg"), reverse=True)[_MAX_FRAMES:]:
                old.unlink(missing_ok=True)

    def export_timelapse(self) -> bytes:
        with self._lock:
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for frame in sorted(self._frames().glob("*.jpg"))[-_MAX_FRAMES:]:
                    if (not self._frames().is_symlink() and not frame.is_symlink()
                            and frame.is_file() and 0 < frame.stat().st_size <= _MAX_JPEG_BYTES):
                        archive.write(frame, frame.name)
            return output.getvalue()

    def _tick(self, settings: dict) -> bool:
        now = time.monotonic()
        interval = settings["timelapse_minutes"] * 60
        if interval != self._interval:
            self._interval = interval
            self._next_frame = now
        due = bool(interval and now >= self._next_frame)
        if due:
            self._next_frame = now + interval
        if not settings["motion"]:
            self._break_observation()
        if camera_policy.is_private(settings) or not (settings["motion"] or due):
            if settings["motion"]:
                self._break_observation()
            with self._lock:
                self._state = "private" if camera_policy.is_private(settings) else "idle"
            return True
        try:
            frame = _fresh_frame()
            if self.stopped.is_set():
                return True
            with camera_policy.lock:
                current = camera_policy.read(tapo.MOMENTS_DIR)
                if current != settings:
                    self._break_observation()
                    return True
                if due:
                    self._write_timelapse(jpeg(frame))
            if settings["motion"]:
                if self._last_frame_at is not None and now - self._last_frame_at > _MAX_PRESENCE_GAP:
                    self._break_observation()
                self._last_frame_at = now
                sample = _motion_sample(frame)
                motion = _has_motion(self._previous, sample)
                capture_due = motion and now - self._last_event >= settings["cooldown_seconds"]
                is_person = self._observe_presence(frame, sample, motion, capture_due, now)
                if capture_due:
                    with camera_policy.lock:
                        if not camera_policy.read(tapo.MOMENTS_DIR)["motion"]:
                            self._break_observation()
                            return True
                        path = tapo.save_moment(frame)
                    rule = settings["presence_rule"] if is_person else "notify"
                    self._last_event = now
                    event = {
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "kind": "person" if is_person else "unclassified",
                        "moment": f"/moments/{path.name}",
                        "rule": rule,
                        "outcome": "notified",
                    }
                    if rule != "notify":
                        try:
                            tapo.require_capture_allowed()
                            if camera_policy.read(tapo.MOMENTS_DIR) == settings:
                                self.on_presence(rule)
                                event["outcome"] = "applied"
                        except Exception:
                            event["outcome"] = "failed"
                    with self._lock:
                        self._events.appendleft(event)
                    self.on_moment()
                self._previous = sample
            with self._lock:
                self._state = "active"
            return True
        except tapo.CameraPrivacyCheckError:
            logger.debug("Camera privacy check unavailable", exc_info=True)
        except tapo.CameraPrivateError:
            self._break_observation()
            with self._lock:
                self._state = "private"
            return True
        except Exception:
            logger.debug("Camera background capture unavailable", exc_info=True)
        self._break_observation()
        with self._lock:
            self._state = "unavailable"
            self._missed += int(due)
        return False

    def run(self) -> None:
        delay = 2
        while not self.stopped.is_set():
            try:
                ok = self._tick(camera_policy.read(tapo.MOMENTS_DIR))
            except (OSError, ValueError):
                self._break_observation()
                with self._lock:
                    self._state = "settings_error"
                ok = False
            delay = 2 if ok else min(60, delay * 2)
            self.stopped.wait(delay)
