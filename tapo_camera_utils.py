"""Tapo camera utilities — RTSP connection and frame capture."""

from __future__ import annotations

import base64
import logging
import os
import time
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

from tooling import brain_tool

load_dotenv()

logger = logging.getLogger(__name__)

_TAPO_IP = os.getenv("TAPO_IP", "")
_TAPO_USERNAME = os.getenv("TAPO_USERNAME", "")
_TAPO_PASSWORD = os.getenv("TAPO_PASSWORD", "")
_TAPO_CLOUD_PASSWORD = os.getenv("TAPO_CLOUD_PASSWORD", "")

MOMENTS_DIR = Path("moments")

_PRIVACY_DISABLE_WAIT_S = 3


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
    required = {
        "TAPO_IP": _TAPO_IP,
        "TAPO_USERNAME": _TAPO_USERNAME,
        "TAPO_PASSWORD": _TAPO_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise TapoError(f"Missing environment variables: {', '.join(missing)}")
    logger.info("Tapo config OK — IP: %s, User: %s", _TAPO_IP, _TAPO_USERNAME)


def get_client() -> Tapo:
    """Authenticates with the Tapo camera API.

    Tries camera-account credentials first, then falls back to cloud auth.

    Raises:
        TapoError: If all authentication methods fail.
    """
    _require_dependencies()
    try:
        client = Tapo(_TAPO_IP, _TAPO_USERNAME, _TAPO_PASSWORD)
        logger.info("Connected to Tapo API at %s (camera account).", _TAPO_IP)
        return client
    except Exception:
        logger.warning("Camera account auth failed — trying cloud fallback.")

    if not _TAPO_CLOUD_PASSWORD:
        raise TapoError("Auth failed. No TAPO_CLOUD_PASSWORD set for fallback.")

    try:
        client = Tapo(
            _TAPO_IP, "admin", _TAPO_CLOUD_PASSWORD,
            cloudPassword=_TAPO_CLOUD_PASSWORD,
        )
        logger.info("Connected to Tapo API at %s (cloud fallback).", _TAPO_IP)
        return client
    except Exception as e:
        raise TapoError(f"All authentication methods failed: {e}") from e


def ensure_camera_on(client: Tapo) -> None:
    """Disables privacy mode if currently active.

    Raises:
        TapoError: If privacy mode cannot be checked or toggled.
    """
    try:
        privacy = client.getPrivacyMode()
        if privacy.get("enabled", "off") == "on":
            logger.info("Privacy mode active — disabling.")
            client.setPrivacyMode(False)
            time.sleep(_PRIVACY_DISABLE_WAIT_S)
            logger.info("Privacy mode disabled.")
        else:
            logger.debug("Camera already active.")
    except Exception as e:
        raise TapoError(f"Failed to manage privacy mode: {e}") from e


def connect(substream: bool = False) -> cv2.VideoCapture | None:
    """Opens an RTSP video stream to the Tapo camera.

    Args:
        substream: Use the low-resolution `stream2` (faster, lighter) instead
            of the full-resolution `stream1`. Good for quick scene analysis.

    Returns:
        An open VideoCapture, or None on failure.
    """
    _require_dependencies()
    stream = "stream2" if substream else "stream1"
    rtsp_url = f"rtsp://{_TAPO_USERNAME}:{_TAPO_PASSWORD}@{_TAPO_IP}:554/{stream}"
    logger.info("Connecting to camera at %s via RTSP (%s).", _TAPO_IP, stream)
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        logger.error("RTSP connection failed. Check credentials and camera IP.")
        return None
    logger.info("RTSP connected.")
    return cap


def capture_moment(cap: cv2.VideoCapture) -> Path | None:
    """Captures a single frame and saves it as a timestamped JPEG.

    Returns:
        Path to the saved file, or None on failure.
    """
    _require_dependencies()
    MOMENTS_DIR.mkdir(exist_ok=True)
    ret, frame = cap.read()
    if not ret:
        logger.error("Failed to read frame from camera.")
        return None
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".jpg"
    filepath = MOMENTS_DIR / filename
    cv2.imwrite(str(filepath), frame)
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
    # Best-effort: make sure privacy mode is off before opening the stream.
    # Capture still proceeds if the control API is unreachable — the RTSP
    # connect below reports the definitive failure.
    try:
        validate_config()
        client = get_client()
        ensure_camera_on(client)
    except Exception as e:
        logger.warning("Privacy-mode check skipped: %s", e)

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


def _detect_people(frame) -> tuple[int, int]:
    """Approximate (people_count, face_count) using built-in OpenCV detectors.

    Runs on a downscaled frame for speed. Full-body HOG + frontal-face Haar —
    good enough for "is someone there?", not a precise census.
    """
    h, w = frame.shape[:2]
    scale = 320.0 / float(w) if w > 320 else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    people = 0
    hog = _get_hog()
    if hog is not None:
        try:
            rects, _ = hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
            people = len(rects)
        except Exception:
            people = 0

    faces = 0
    casc = _get_face_cascade()
    if casc is not None and not casc.empty():
        try:
            faces = len(casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5))
        except Exception:
            faces = 0
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
    # Best-effort: make sure privacy mode is off; capture proceeds regardless.
    try:
        ensure_camera_on(get_client())
    except Exception as e:
        logger.warning("Privacy-mode check skipped: %s", e)

    cap = connect(substream=True)   # low-res stream is plenty for scene facts
    if not cap:
        raise TapoError("Unable to open RTSP stream to look around.")
    try:
        frame = None
        for _ in range(5):          # flush stale buffered frames first
            ok, f = cap.read()
            if ok:
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
