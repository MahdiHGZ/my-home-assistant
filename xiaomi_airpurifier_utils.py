"""Xiaomi Air Purifier 4 Lite (MIoT) utilities — local network control.

Target model  : zhimi.airp.mb5
Protocol      : MIoT (python-miio MiotDevice)
Spec reference: https://home.miot-spec.com/spec/zhimi.airp.mb5

Set AIRPURIFIER_IP and AIRPURIFIER_TOKEN in .env (32-char hex token
from Xiaomi Cloud Tokens Extractor).

MIoT service layout for this device:
  siid 1  — Device Information
  siid 2  — Air Purifier  (power, mode, fan level, anion)
  siid 3  — Environment   (humidity, PM2.5, temperature)
  siid 4  — Filter        (life level, used/left time, reset)
  siid 6  — Alarm         (buzzer on/off)
  siid 8  — Physical Control Lock (child lock)
  siid 9  — Custom        (motor RPM, favorite speed/level, manual level)
  siid 10 — Filter Time   (debug write)
  siid 11 — AQI           (purify volume, average AQI, AQI state)
  siid 12 — RFID          (filter chip identification)
  siid 13 — Screen        (LED brightness)
  siid 14 — Display Unit  (°C / °F)
"""

from __future__ import annotations

import logging
import os
from enum import IntEnum
from typing import Any

from dotenv import load_dotenv

from miot_base import MiotHelper
from tooling import mark_tool_functions

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PURIFIER_IP = os.getenv("AIRPURIFIER_IP", "")
_PURIFIER_TOKEN = os.getenv("AIRPURIFIER_TOKEN", "")
PURIFIER_MODEL = "zhimi.airp.mb5"


class PurifierError(Exception):
    """Raised when an air purifier operation fails."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Fault(IntEnum):
    NONE = 0
    SENSOR_PM_ERROR = 1
    TEMP_ERROR = 2
    HUM_ERROR = 3
    NO_FILTER = 4


class Mode(IntEnum):
    AUTO = 0
    SLEEP = 1
    FAVORITE = 2
    MANUAL = 3


class FanLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class ScreenBrightness(IntEnum):
    OFF = 0
    BRIGHT = 1
    BRIGHTEST = 2


class TempUnit(IntEnum):
    CELSIUS = 1
    FAHRENHEIT = 2


class AqiState(IntEnum):
    GOOD_LOW = 0
    GOOD_HIGH = 1
    MODERATE_LOW = 2
    MODERATE_HIGH = 3
    BAD_LOW = 4
    BAD_HIGH = 5


# ---------------------------------------------------------------------------
# MIoT property map — (siid, piid)
# ---------------------------------------------------------------------------

PROP = {
    # siid 2 — air purifier
    "power":            (2, 1),
    "fault":            (2, 2),
    "mode":             (2, 4),
    "fan_level":        (2, 5),
    "anion":            (2, 6),
    # siid 3 — environment
    "humidity":         (3, 1),
    "pm25":             (3, 4),
    "temperature":      (3, 7),
    # siid 4 — filter
    "filter_life":      (4, 1),
    "filter_used_hours": (4, 3),
    "filter_left_days": (4, 4),
    # siid 6 — alarm
    "buzzer":           (6, 1),
    # siid 8 — child lock
    "child_lock":       (8, 1),
    # siid 9 — custom
    "motor_rpm":        (9, 1),
    "favorite_speed":   (9, 2),
    "motor_set_speed":  (9, 4),
    "favorite_level":   (9, 5),
    "bottom_door":      (9, 6),
    "reboot_cause":     (9, 8),
    "manual_level":     (9, 9),
    "country_code":     (9, 10),
    "iic_error_count":  (9, 11),
    # siid 11 — AQI
    "purify_volume":    (11, 1),
    "average_aqi":      (11, 2),
    "aqi_state":        (11, 3),
    "aqi_heartbeat":    (11, 4),
    # siid 12 — RFID
    "rfid_tag":         (12, 1),
    "rfid_factory_id":  (12, 2),
    "rfid_product_id":  (12, 3),
    "rfid_time":        (12, 4),
    "rfid_serial":      (12, 5),
    # siid 13 — screen
    "screen_brightness": (13, 2),
    # siid 14 — display unit
    "temp_unit":        (14, 1),
}

# ---------------------------------------------------------------------------
# MIoT action map — (siid, aiid)
# ---------------------------------------------------------------------------

ACTION = {
    "toggle":           (2, 1),
    "toggle_mode":      (9, 1),
    "toggle_fan_level": (9, 2),
    "reset_filter":     (4, 1),
}


# ---------------------------------------------------------------------------
# Connection / property / action plumbing (shared with the vacuum)
# ---------------------------------------------------------------------------

_HELPER = MiotHelper(
    label="Purifier",
    ip=_PURIFIER_IP,
    token=_PURIFIER_TOKEN,
    model=PURIFIER_MODEL,
    prop_map=PROP,
    action_map=ACTION,
    error_cls=PurifierError,
    env_vars={
        "AIRPURIFIER_IP": _PURIFIER_IP,
        "AIRPURIFIER_TOKEN": _PURIFIER_TOKEN,
    },
    token_var="AIRPURIFIER_TOKEN",
)


def validate_config() -> None:
    """Validate required purifier connection env vars before device calls."""
    _HELPER.validate_config()


def get_device():
    """Returns a (cached) MiotDevice for the air purifier."""
    return _HELPER.get_device()


def reset_device() -> None:
    """Drops the cached device so the next command reconnects."""
    _HELPER.invalidate()


def _ensure(device):
    return _HELPER.ensure(device)


def _get_prop(device, name: str) -> Any:
    return _HELPER.get_prop(device, name)


def _set_prop(device, name: str, value: Any) -> Any:
    return _HELPER.set_prop(device, name, value)


def _call_action(device, action_name: str, params: list[dict[str, Any]] | None = None) -> Any:
    return _HELPER.call_action(device, action_name, params)


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------

def turn_on(device=None) -> Any:
    """Turns the air purifier ON."""
    device = _ensure(device)
    logger.info("Purifier: ON.")
    return _set_prop(device, "power", True)


def turn_off(device=None) -> Any:
    """Turns the air purifier OFF."""
    device = _ensure(device)
    logger.info("Purifier: OFF.")
    return _set_prop(device, "power", False)


def toggle(device=None) -> Any:
    """Toggles the air purifier power."""
    device = _ensure(device)
    logger.info("Purifier: toggle.")
    return _call_action(device, "toggle")


def is_on(device=None) -> bool:
    """Returns True if the purifier is currently on."""
    device = _ensure(device)
    return bool(_get_prop(device, "power"))


# ---------------------------------------------------------------------------
# Mode & fan
# ---------------------------------------------------------------------------

def set_mode(mode: Mode | int, device=None) -> Any:
    """Sets the operating mode.

    Args:
        mode: Mode enum or int (0=Auto, 1=Sleep, 2=Favorite, 3=Manual).
    """
    device = _ensure(device)
    mode = Mode(mode)
    logger.info("Purifier: mode → %s.", mode.name)
    return _set_prop(device, "mode", mode.value)


def set_auto(device=None) -> Any:
    """Shortcut: switch to Auto mode."""
    return set_mode(Mode.AUTO, device)


def set_sleep(device=None) -> Any:
    """Shortcut: switch to Sleep mode."""
    return set_mode(Mode.SLEEP, device)


def set_favorite(device=None) -> Any:
    """Shortcut: switch to Favorite mode (uses favorite_speed/level)."""
    return set_mode(Mode.FAVORITE, device)


def set_manual(device=None) -> Any:
    """Shortcut: switch to Manual mode."""
    return set_mode(Mode.MANUAL, device)


def toggle_mode(device=None) -> Any:
    """Cycles to the next operating mode."""
    device = _ensure(device)
    logger.info("Purifier: toggle mode.")
    return _call_action(device, "toggle_mode")


def set_fan_level(level: FanLevel | int, device=None) -> Any:
    """Sets the fan speed level.

    Args:
        level: FanLevel enum or int (1=Low, 2=Medium, 3=High).
    """
    device = _ensure(device)
    level = FanLevel(level)
    logger.info("Purifier: fan level → %s.", level.name)
    return _set_prop(device, "fan_level", level.value)


def toggle_fan_level(device=None) -> Any:
    """Cycles to the next fan level."""
    device = _ensure(device)
    logger.info("Purifier: toggle fan level.")
    return _call_action(device, "toggle_fan_level")


def set_manual_level(level: int, device=None) -> Any:
    """Sets manual fan level (1–3), only effective in Manual mode.

    Args:
        level: 1 (Low), 2 (Medium), or 3 (High).
    """
    device = _ensure(device)
    level = max(1, min(3, level))
    logger.info("Purifier: manual level → %d.", level)
    return _set_prop(device, "manual_level", level)


# ---------------------------------------------------------------------------
# Favorite speed / level
# ---------------------------------------------------------------------------

def set_favorite_speed(rpm: int, device=None) -> Any:
    """Sets the favorite motor speed (200–2300 RPM).

    Only applies when mode is Favorite.

    Args:
        rpm: Target RPM, 200–2300.
    """
    device = _ensure(device)
    rpm = max(200, min(2300, rpm))
    logger.info("Purifier: favorite speed → %d RPM.", rpm)
    return _set_prop(device, "favorite_speed", rpm)


def set_favorite_level(level: int, device=None) -> Any:
    """Sets the favorite level preset (0–11).

    A higher-level abstraction over favorite_speed.

    Args:
        level: 0 (lowest) to 11 (highest).
    """
    device = _ensure(device)
    level = max(0, min(11, level))
    logger.info("Purifier: favorite level → %d.", level)
    return _set_prop(device, "favorite_level", level)


# ---------------------------------------------------------------------------
# Anion (ionizer)
# ---------------------------------------------------------------------------

def set_anion(enabled: bool, device=None) -> Any:
    """Enables/disables the negative-ion generator (ionizer)."""
    device = _ensure(device)
    logger.info("Purifier: anion → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "anion", enabled)


# ---------------------------------------------------------------------------
# Buzzer / alarm
# ---------------------------------------------------------------------------

def set_buzzer(enabled: bool, device=None) -> Any:
    """Enables/disables the buzzer (key-press beeps)."""
    device = _ensure(device)
    logger.info("Purifier: buzzer → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "buzzer", enabled)


# ---------------------------------------------------------------------------
# Child lock
# ---------------------------------------------------------------------------

def set_child_lock(enabled: bool, device=None) -> Any:
    """Enables/disables the physical button lock."""
    device = _ensure(device)
    logger.info("Purifier: child lock → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "child_lock", enabled)


# ---------------------------------------------------------------------------
# Screen / LED
# ---------------------------------------------------------------------------

def set_screen_brightness(level: ScreenBrightness | int, device=None) -> Any:
    """Sets the display LED brightness.

    Args:
        level: ScreenBrightness enum or int (0=Off, 1=Bright, 2=Brightest).
    """
    device = _ensure(device)
    level = ScreenBrightness(level)
    logger.info("Purifier: screen → %s.", level.name)
    return _set_prop(device, "screen_brightness", level.value)


def screen_off(device=None) -> Any:
    """Turns off the display LED."""
    return set_screen_brightness(ScreenBrightness.OFF, device)


def screen_on(device=None) -> Any:
    """Sets display LED to brightest."""
    return set_screen_brightness(ScreenBrightness.BRIGHTEST, device)


# ---------------------------------------------------------------------------
# Temperature display unit
# ---------------------------------------------------------------------------

def set_temp_unit(unit: TempUnit | int, device=None) -> Any:
    """Sets the temperature display unit.

    Args:
        unit: TempUnit enum or int (1=Celsius, 2=Fahrenheit).
    """
    device = _ensure(device)
    unit = TempUnit(unit)
    logger.info("Purifier: temp unit → %s.", unit.name)
    return _set_prop(device, "temp_unit", unit.value)


# ---------------------------------------------------------------------------
# Environment sensors
# ---------------------------------------------------------------------------

def get_pm25(device=None) -> float:
    """Returns the current PM2.5 reading (μg/m³)."""
    device = _ensure(device)
    return _get_prop(device, "pm25")


def get_humidity(device=None) -> int:
    """Returns relative humidity (0–100%)."""
    device = _ensure(device)
    return _get_prop(device, "humidity")


def get_temperature(device=None) -> float:
    """Returns ambient temperature (°C)."""
    device = _ensure(device)
    return _get_prop(device, "temperature")


def get_environment(device=None) -> dict[str, Any]:
    """Returns all environment sensor readings at once."""
    device = _ensure(device)
    return _HELPER.get_props(device, ["pm25", "humidity", "temperature"])


# ---------------------------------------------------------------------------
# AQI stats
# ---------------------------------------------------------------------------

def get_aqi_state(device=None) -> str:
    """Returns a human-readable AQI quality label."""
    device = _ensure(device)
    raw = _get_prop(device, "aqi_state")
    try:
        return AqiState(raw).name.replace("_", " ").title()
    except ValueError:
        return f"Unknown({raw})"


def get_average_aqi(device=None) -> int:
    """Returns the average AQI value (0–600)."""
    device = _ensure(device)
    return _get_prop(device, "average_aqi")


def get_purify_volume(device=None) -> int:
    """Returns cumulative purified air volume (m³)."""
    device = _ensure(device)
    return _get_prop(device, "purify_volume")


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def get_filter_status(device=None) -> dict[str, Any]:
    """Returns filter life percentage, hours used, and days remaining."""
    device = _ensure(device)
    return _HELPER.get_props(
        device, ["filter_life", "filter_used_hours", "filter_left_days"]
    )


def reset_filter(device=None) -> Any:
    """Resets the filter life counter (call after replacing the filter).

    Sends the current used-hours value as required by the spec.
    """
    device = _ensure(device)
    used_hours = _get_prop(device, "filter_used_hours")
    logger.info("Purifier: reset filter (used %d hours).", used_hours)
    siid, aiid = ACTION["reset_filter"]
    return device.call_action_by(siid, aiid, [{"piid": 3, "value": used_hours}])


# ---------------------------------------------------------------------------
# RFID (filter chip identification)
# ---------------------------------------------------------------------------

def get_filter_rfid(device=None) -> dict[str, Any]:
    """Returns RFID chip data from the installed filter."""
    device = _ensure(device)
    return _HELPER.get_props(
        device,
        ["rfid_tag", "rfid_factory_id", "rfid_product_id", "rfid_time", "rfid_serial"],
    )


# ---------------------------------------------------------------------------
# Motor info
# ---------------------------------------------------------------------------

def get_motor_speed(device=None) -> int:
    """Returns the current motor speed (RPM)."""
    device = _ensure(device)
    return _get_prop(device, "motor_rpm")


# ---------------------------------------------------------------------------
# Comprehensive status
# ---------------------------------------------------------------------------

_ENUM_MAP: dict[str, type[IntEnum]] = {
    "fault": Fault,
    "mode": Mode,
    "fan_level": FanLevel,
    "screen_brightness": ScreenBrightness,
    "aqi_state": AqiState,
}


def get_status(device=None) -> dict[str, Any]:
    """Reads all key purifier properties into a single dict.

    Returns dict with keys like power, mode, fan_level, pm25,
    temperature, humidity, filter_life, etc.
    Enum values are resolved to readable names.
    """
    device = _ensure(device)
    keys = [
        "power", "fault", "mode", "fan_level", "anion",
        "pm25", "humidity", "temperature",
        "filter_life", "filter_used_hours", "filter_left_days",
        "buzzer", "child_lock", "screen_brightness",
        "motor_rpm", "favorite_speed", "favorite_level",
        "average_aqi", "aqi_state", "purify_volume",
    ]
    result: dict[str, Any] = _HELPER.get_props(device, keys)

    for key, enum_cls in _ENUM_MAP.items():
        raw = result.get(key)
        if raw is None:
            continue
        try:
            result[key] = enum_cls(raw).name.replace("_", " ").title()
        except ValueError:
            pass

    if result.get("power") is not None:
        result["power"] = "ON" if result["power"] else "OFF"
    for bool_key in ("anion", "buzzer", "child_lock"):
        if result.get(bool_key) is not None:
            result[bool_key] = "ON" if result[bool_key] else "OFF"

    return result


# ---------------------------------------------------------------------------
# Tool registration hints for LLM orchestration
# ---------------------------------------------------------------------------

# Only these functions are marked for tool registration in brain_test.py.
TOOL_FUNCTIONS = {
    "get_status",
    "get_environment",
    "get_pm25",
    "get_humidity",
    "get_temperature",
    "get_aqi_state",
    "get_average_aqi",
    "get_filter_status",
    "turn_on",
    "turn_off",
    "set_mode",
    "set_auto",
    "set_sleep",
    "set_favorite",
    "set_fan_level",
    "set_anion",
    "set_buzzer",
    "set_child_lock",
    "set_screen_brightness",
    "set_temp_unit",
}

mark_tool_functions(globals(), TOOL_FUNCTIONS)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    validate_config()
    d = get_device()

    print("=== Status ===")
    for k, v in get_status(d).items():
        print(f"  {k:20s}: {v}")

    print("\n=== Environment ===")
    env = get_environment(d)
    print(f"  PM2.5      : {env['pm25']} μg/m³")
    print(f"  Humidity   : {env['humidity']}%")
    print(f"  Temperature: {env['temperature']}°C")

    print("\n=== Filter ===")
    flt = get_filter_status(d)
    print(f"  Life       : {flt['filter_life']}%")
    print(f"  Used       : {flt['filter_used_hours']} hours")
    print(f"  Remaining  : {flt['filter_left_days']} days")

    print(f"\n=== AQI: {get_aqi_state(d)} (avg {get_average_aqi(d)}) ===")
