"""Tapo camera utilities — RTSP connection and frame capture."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
import uuid
from urllib.parse import quote
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    import cv2
    _CV2_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    cv2 = None
    _CV2_IMPORT_ERROR = exc

try:
    from pytapo import Tapo
    _PYTAPO_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    Tapo = Any  # type: ignore[assignment]
    _PYTAPO_IMPORT_ERROR = exc

import camera_policy
from tooling import brain_tool

load_dotenv()

logger = logging.getLogger(__name__)

_credentials = tuple(os.getenv(key, "") for key in ("TAPO_IP", "TAPO_USERNAME", "TAPO_PASSWORD", "TAPO_CLOUD_PASSWORD"))


def credentials():
    return _credentials


def set_credentials(values):
    global _credentials
    _credentials = values


MOMENTS_DIR = Path("moments")
moments_lock = threading.Lock()
_MAX_MOMENTS = 50

class TapoError(Exception):
    """Raised when a Tapo camera operation fails."""


def _require_dependencies() -> None:
    missing: list[str] = []
    if _CV2_IMPORT_ERROR is not None:
        missing.append("opencv-python")
    if _PYTAPO_IMPORT_ERROR is not None:
        missing.append("pytapo")
    if missing:
        raise TapoError(
            "Missing dependencies for Tapo camera tools: "
            + ", ".join(missing)
            + ". Install them with pip."
        )


def validate_config() -> None:
    """Ensures required Tapo environment variables are set.

    Raises:
        TapoError: If any required variable is missing.
    """
    host, user, password, _ = credentials()
    required = {
        "TAPO_IP": host,
        "TAPO_USERNAME": user,
        "TAPO_PASSWORD": password,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise TapoError(f"Missing environment variables: {', '.join(missing)}")
    logger.info("Tapo config OK — IP: %s, User: %s", host, user)


def get_client(config=None) -> Tapo:
    """Authenticates with the Tapo camera API.

    Tries camera-account credentials first, then falls back to cloud auth.

    Raises:
        TapoError: If all authentication methods fail.
    """
    host, user, password, cloud_password = config or credentials()
    _require_dependencies()
    try:
        client = Tapo(host, user, password)
        logger.info("Connected to Tapo API at %s (camera account).", host)
        return client
    except Exception:
        logger.warning("Camera account auth failed — trying cloud fallback.")

    if not cloud_password:
        raise TapoError("Auth failed. No TAPO_CLOUD_PASSWORD set for fallback.")

    try:
        client = Tapo(
            host, "admin", cloud_password,
            cloudPassword=cloud_password,
        )
        logger.info("Connected to Tapo API at %s (cloud fallback).", host)
        return client
    except Exception as e:
        raise TapoError(f"All authentication methods failed: {e}") from e


class CameraPrivateError(TapoError):
    """Capture denied by the local policy or the camera's privacy mode."""


class CameraPrivacyCheckError(CameraPrivateError):
    """Capture denied because the camera's privacy state is unavailable."""


def require_capture_allowed() -> None:
    try:
        private = camera_policy.is_private(camera_policy.read(MOMENTS_DIR))
    except (OSError, ValueError) as exc:
        raise CameraPrivateError("Camera privacy settings are unreadable.") from exc
    if private:
        raise CameraPrivateError("Camera capture is blocked by the local privacy policy.")


def ensure_camera_on(client: Tapo) -> None:
    """Check the physical privacy mode without changing it."""
    try:
        enabled = client.getPrivacyMode().get("enabled")
    except Exception as exc:
        raise CameraPrivacyCheckError("Camera privacy mode could not be verified.") from exc
    if enabled == "on":
        raise CameraPrivateError("The camera reports privacy mode is enabled.")
    if enabled != "off":
        raise CameraPrivacyCheckError("The camera returned an unknown privacy state.")


_last_connection = None


def availability() -> dict:
    config = credentials()
    if not all(config[:3]):
        return {"available": False, "reason": "unconfigured"}
    previous = _last_connection
    if previous and previous[0] is config and time.monotonic() - previous[1] <= 120:
        return {"available": previous[2]}
    return {"available": None}


def connect(substream: bool = False, *, config=None, client=None) -> cv2.VideoCapture | None:
    global _last_connection
    config = credentials() if config is None else config
    try:
        cap = _connect(config, substream, client)
    except CameraPrivateError:
        # Privacy is an intentional operating state, and checking it must never
        # cause the dashboard to open another connection.
        _last_connection = (config, time.monotonic(), None)
        raise
    except Exception:
        _last_connection = (config, time.monotonic(), False)
        raise
    _last_connection = (config, time.monotonic(), cap is not None)
    return cap


def _connect(config, substream: bool, client=None) -> cv2.VideoCapture | None:
    """Opens an RTSP video stream to the Tapo camera.

    Args:
        substream: Use the low-resolution `stream2` (faster, lighter) instead
            of the full-resolution `stream1`. Good for quick scene analysis.

    Returns:
        An open VideoCapture, or None on failure.
    """
    _require_dependencies()
    require_capture_allowed()
    host, user, password, _ = config
    ensure_camera_on(get_client(config) if client is None else client)
    require_capture_allowed()
    stream = "stream2" if substream else "stream1"
    rtsp_url = f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:554/{stream}"
    logger.info("Connecting to camera at %s via RTSP (%s).", host, stream)
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
    ])
    if not cap.isOpened():
        cap.release()
        logger.error("RTSP connection failed. Check credentials and camera IP.")
        return None
    try:
        require_capture_allowed()
    except CameraPrivateError:
        cap.release()
        raise
    logger.info("RTSP connected.")
    return cap


def _atomic_imwrite(path: Path, frame, *, capture: bool = False) -> None:
    """Encode beside the destination and publish only a complete image."""
    temp_path = path.parent / f".{path.stem}.{uuid.uuid4().hex}.tmp{path.suffix}"
    try:
        if not cv2.imwrite(str(temp_path), frame):
            raise TapoError(f"Failed to encode image: {path}")
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise TapoError(f"Image encoder produced an empty file: {path}")
        if capture:
            require_capture_allowed()
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def moment_caption_path(original: Path) -> Path:
    return original.parent / f".{original.name}.caption"


def delete_moment_derivatives(original: Path) -> None:
    thumbs = original.parent / ".thumbs"
    if not thumbs.is_symlink():
        (thumbs / original.name).unlink(missing_ok=True)
    moment_caption_path(original).unlink(missing_ok=True)


def _prune_moments() -> None:
    files = sorted(
        (
            path for path in MOMENTS_DIR.iterdir()
            if not path.name.startswith(".") and path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[_MAX_MOMENTS:]:
        try:
            path.unlink(missing_ok=True)
            delete_moment_derivatives(path)
        except OSError as exc:
            logger.warning("Could not prune old moment %s: %s", path.name, exc)


def capture_moment(cap: cv2.VideoCapture) -> Path | None:
    """Captures a single frame and saves it as a timestamped JPEG.

    Returns:
        Path to the saved file, or None on failure.
    """
    _require_dependencies()
    frame = read_frame(cap)
    if frame is None:
        logger.error("Failed to read frame from camera.")
        return None
    return save_moment(frame)


def read_frame(cap):
    require_capture_allowed()
    ok, frame = cap.read()
    require_capture_allowed()
    return frame if ok and frame is not None and frame.size else None


def _write_thumbnail(path: Path, frame) -> None:
    if path.parent.is_symlink():
        raise OSError("Invalid thumbnail directory.")
    h, w = frame.shape[:2]
    size = (256, max(1, round(256 * h / w)))
    thumbnail = cv2.resize(frame, size, interpolation=cv2.INTER_AREA) if w > 256 else frame
    path.parent.mkdir(exist_ok=True)
    _atomic_imwrite(path, thumbnail)


def thumbnail_for(original: Path) -> Path:
    """Called under moments_lock; fall back to the original if decoding fails."""
    preview = original.parent / ".thumbs" / original.name
    if preview.parent.is_symlink() or preview.is_symlink():
        return original
    if preview.is_file() and preview.stat().st_size:
        return preview
    if cv2 is None:
        return original
    try:
        frame = cv2.imread(str(original))
        if frame is None:
            return original
        _write_thumbnail(preview, frame)
        return preview
    except (OSError, TapoError, cv2.error):
        return original


def save_moment(frame) -> Path:
    with camera_policy.lock, moments_lock:
        require_capture_allowed()
        MOMENTS_DIR.mkdir(parents=True, exist_ok=True)
        (MOMENTS_DIR / ".thumbs").mkdir(exist_ok=True)
        filename = (
            datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            + f"-{uuid.uuid4().hex[:8]}.jpg"
        )
        filepath = MOMENTS_DIR / filename
        _atomic_imwrite(filepath, frame, capture=True)

        # Thumbnail failure must not hide a successfully committed original.
        try:
            _write_thumbnail(MOMENTS_DIR / ".thumbs" / filename, frame)
        except Exception as exc:
            logger.warning("Thumbnail creation failed for %s: %s", filename, exc)
        _prune_moments()

    logger.info("Moment saved: %s", filepath)
    return filepath


def _encode_image_data_uri(
    image_path: Path,
    max_width: int = 1024,
    jpeg_quality: int = 75,
) -> str:
    """Encode an image file as a compact JPEG data URI."""
    _require_dependencies()
    image = cv2.imread(str(image_path))
    if image is None:
        raise TapoError(f"Could not read image file: {image_path}")

    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        resized = cv2.resize(image, (max_width, int(height * scale)))
        image = resized

    ok, encoded = cv2.imencode(
        ".jpg",
        image,
        [int(cv2.IMWRITE_JPEG_QUALITY), max(20, min(95, int(jpeg_quality)))],
    )
    if not ok:
        raise TapoError(f"Failed to encode image: {image_path}")

    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


@brain_tool
def capture_moment_for_model(
    include_data_uri: bool = True,
    max_width: int = 1024,
    jpeg_quality: int = 75,
) -> dict[str, object]:
    """Capture a fresh camera frame and return media payload for the model.

    Intended for tool use by an agent/model:
    - Captures one "moment" image from the Tapo camera.
    - Returns local file path and metadata.
    - Optionally returns an `image_url` data URI that can be passed directly as
      media input to multimodal chat models.

    Args:
        include_data_uri: If True, include `media_input` with image data URI.
            Keep False for smaller tool outputs; the caller can convert later.
        max_width: Max image width used when encoding `media_input`.
        jpeg_quality: JPEG quality (20-95) used for encoded `media_input`.

    Returns:
        Dict containing:
        - ok: True when capture succeeds.
        - image_path: Absolute snapshot path.
        - captured_at: ISO timestamp.
        - media_input: Optional image content part for model input.
    """
    cap = connect()
    if not cap:
        raise TapoError("Unable to open RTSP stream for capture.")

    try:
        image_path = capture_moment(cap)
    finally:
        cap.release()

    if image_path is None:
        raise TapoError("Failed to capture image frame.")

    image_path = image_path.resolve()
    result: dict[str, object] = {
        "ok": True,
        "image_path": str(image_path),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        # Lightweight pointer used by brain.run_prompt() media injection.
        "media_input": {"type": "image_path", "path": str(image_path)},
    }

    if include_data_uri:
        result["media_input"] = {
            "type": "image_url",
            "image_url": {
                "url": _encode_image_data_uri(
                    image_path,
                    max_width=max_width,
                    jpeg_quality=jpeg_quality,
                )
            },
        }

    return result


# ---------------------------------------------------------------------------
# Scene awareness — text facts the (text-only) LLM can read about the room.
# Pure OpenCV, no extra model downloads: mean-luminance for lighting,
# built-in HOG + Haar detectors for people/faces, frame-diff for change.
# ---------------------------------------------------------------------------

_hog = None                 # lazily-built HOG people detector
_face_cascade = None        # lazily-built Haar face detector
_last_scene_gray = None     # previous small grayscale frame, for change detection


def _get_hog():
    global _hog
    if _hog is None and cv2 is not None:
        _hog = cv2.HOGDescriptor()
        _hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return _hog


def _get_face_cascade():
    global _face_cascade
    if _face_cascade is None and cv2 is not None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(path)
    return _face_cascade


def _brightness_label(mean: float) -> str:
    """Map a 0-255 mean luminance to a human word."""
    if mean < 40:
        return "dark"
    if mean < 85:
        return "dim"
    if mean < 165:
        return "well lit"
    return "bright"


def _detect_people(frame, *, require_complete: bool = False) -> tuple[int, int]:
    """Approximate (people_count, face_count) using built-in OpenCV detectors.

    Runs on a downscaled frame for speed. Full-body HOG + frontal-face Haar —
    good enough for "is someone there?", not a precise census.
    """
    h, w = frame.shape[:2]
    scale = 320.0 / float(w) if w > 320 else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
    if require_complete and (small.shape[0] < 128 or small.shape[1] < 64):
        raise TapoError("Camera frame is too small for a complete person observation.")
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    people = 0
    hog = _get_hog()
    if hog is not None:
        try:
            rects, _ = hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
            people = len(rects)
        except Exception:
            if require_complete:
                raise
    elif require_complete:
        raise TapoError("Person detector unavailable.")

    faces = 0
    casc = _get_face_cascade()
    if casc is not None and not casc.empty():
        try:
            faces = len(casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5))
        except Exception:
            if require_complete:
                raise
    elif require_complete:
        raise TapoError("Face detector unavailable.")
    return people, faces


def analyze_frame(frame) -> dict:
    """Analyze one BGR frame into text-friendly environment facts."""
    global _last_scene_gray
    gray_small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 120))
    mean = float(gray_small.mean())
    label = _brightness_label(mean)
    pct = round(mean / 255 * 100)

    changed = None
    if _last_scene_gray is not None and _last_scene_gray.shape == gray_small.shape:
        diff = float(cv2.absdiff(gray_small, _last_scene_gray).mean())
        changed = diff > 12.0
    _last_scene_gray = gray_small

    people, faces = _detect_people(frame)
    present = people > 0 or faces > 0

    parts = [f"The room looks {label} (~{pct}% brightness)."]
    if present:
        who = []
        if people:
            who.append(f"{people} person(s)")
        if faces:
            who.append(f"{faces} face(s)")
        parts.append("Someone is visible: " + ", ".join(who) + ".")
    else:
        parts.append("No people visible.")
    if changed is True:
        parts.append("The scene changed since the last look.")

    return {
        "ok": True,
        "lighting": label,
        "brightness_pct": pct,
        "people": people,
        "faces": faces,
        "someone_present": present,
        "changed_since_last": changed,
        "summary": " ".join(parts),
    }


@brain_tool
def look_around() -> dict[str, object]:
    """Look through the camera and describe the room in words.

    Captures one frame and reports lighting (dark/dim/well lit/bright),
    whether any people or faces are visible, and whether the scene changed
    since the last look. Use this to sense the environment — you cannot see
    the raw image, only these facts.

    Returns:
        Dict with: ok, lighting, brightness_pct, people, faces,
        someone_present, changed_since_last, summary.
    """
    _require_dependencies()
    cap = connect(substream=True)   # low-res stream is plenty for scene facts
    if not cap:
        raise TapoError("Unable to open RTSP stream to look around.")
    try:
        frame = None
        for _ in range(5):          # flush stale buffered frames first
            f = read_frame(cap)
            if f is not None:
                frame = f
    finally:
        cap.release()

    if frame is None:
        raise TapoError("Failed to read a camera frame.")
    return analyze_frame(frame)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    validate_config()
    print(look_around())
