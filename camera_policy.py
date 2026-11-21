"""Local capture policy shared by all camera entry points."""

from __future__ import annotations

import json
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

lock = threading.RLock()
DEFAULT = {
    "windows": [],
    "override": "schedule",
    "motion": False,
    "cooldown_seconds": 60,
    "timelapse_minutes": 0,
    "presence_rule": "notify",
    "daily_digest": False,
}


def validate(settings: dict) -> dict:
    if set(settings) != set(DEFAULT):
        raise ValueError("Incomplete camera settings.")
    windows = settings["windows"]
    if not isinstance(windows, list) or len(windows) > 12:
        raise ValueError("Enter up to 12 privacy intervals.")
    for row in windows:
        if (not isinstance(row, list) or len(row) != 2
                or any(not isinstance(t, str) or not re.fullmatch(
                    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", t
                ) for t in row) or row[0] == row[1]):
            raise ValueError("Privacy intervals require distinct HH:MM times.")
    if settings["override"] not in ("schedule", "private", "allow"):
        raise ValueError("Invalid privacy override.")
    if settings["presence_rule"] not in ("notify", "lights_on", "vacuum_pause"):
        raise ValueError("Invalid camera presence rule.")
    if type(settings["daily_digest"]) is not bool:
        raise ValueError("Invalid daily digest setting.")
    if type(settings["motion"]) is not bool:
        raise ValueError("Invalid motion setting.")
    for field, low, high in (("cooldown_seconds", 15, 3600), ("timelapse_minutes", 0, 1440)):
        if type(settings[field]) is not int or not low <= settings[field] <= high:
            raise ValueError(f"Invalid {field}.")
    return settings


def read(directory: Path) -> dict:
    path = directory / ".camera-settings.json"
    with lock:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {**DEFAULT, "windows": []}
        if not isinstance(value, dict):
            raise ValueError("Invalid camera settings.")
        return validate(value)


def write(directory: Path, settings: dict) -> None:
    validate(settings)
    with lock:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".camera-", delete=False
        ) as output:
            path = Path(output.name)
            try:
                json.dump(settings, output)
                output.close()
                path.replace(directory / ".camera-settings.json")
            finally:
                path.unlink(missing_ok=True)


def is_private(settings: dict, now: datetime | None = None) -> bool:
    if settings["override"] != "schedule":
        return settings["override"] == "private"
    local_time = (now or datetime.now()).strftime("%H:%M")
    for start, end in settings["windows"]:
        if start < end and start <= local_time < end:
            return True
        if end < start and (local_time >= start or local_time < end):
            return True
    return False
