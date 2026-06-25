"""Xiaomi Vacuum S20 (MIoT) utilities — local network control.

Target model  : xiaomi.vacuum.d106gl
Protocol      : MIoT (python-miio MiotDevice)
Spec reference: https://home.miot-spec.com/spec/xiaomi.vacuum.d106gl

Set VACUUM_IP and VACUUM_TOKEN in .env (32-char hex token from
Xiaomi Cloud Tokens Extractor).

MIoT service layout for this device:
  siid 1  — Device Information
  siid 2  — Robot Cleaner (status, mode, suction, sweep/mop actions)
  siid 3  — Battery (level, start-charge)
  siid 4  — Alarm (find-me, volume)
  siid 7  — Sweep extended (consumables, remote control, settings)
  siid 8  — Scheduled orders
  siid 9  — Point / zone cleaning
  siid 10 — Map management
  siid 12 — Do-not-disturb
  siid 14 — Language / voice packs
  siid 15 — Filter
  siid 16 — Main brush
  siid 17 — Side brush
  siid 18 — Mop
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

_VACUUM_IP = os.getenv("VACUUM_IP", "")
_VACUUM_TOKEN = os.getenv("VACUUM_TOKEN", "")
VACUUM_MODEL = "xiaomi.vacuum.d106gl"


class VacuumError(Exception):
    """Raised when a vacuum operation fails."""


# ---------------------------------------------------------------------------
# Enums — mirror MIoT value-lists so callers never need raw ints
# ---------------------------------------------------------------------------

class Status(IntEnum):
    SLEEP = 0
    IDLE = 1
    PAUSED = 2
    GO_CHARGING = 3
    CHARGING = 4
    SWEEPING = 5
    SWEEPING_AND_MOPPING = 6
    MOPPING = 7
    UPGRADING = 8
    BUILDING_MAP = 9
    CHARGING_COMPLETE = 10


class CleanMode(IntEnum):
    SWEEP = 0
    SWEEP_AND_MOP = 1
    MOP = 2
    SWEEP_THEN_MOP = 3


class SweepType(IntEnum):
    GLOBAL = 0
    MOP = 1
    EDGE = 2
    AREA = 3
    POINT = 4
    REMOTE = 5
    EXPLORE = 6
    ROOM = 7
    FLOOR = 8


class SuctionLevel(IntEnum):
    SILENT = 0
    BASIC = 1
    STRONG = 2
    FULL_SPEED = 3


class WaterLevel(IntEnum):
    LOW = 0
    MID = 1
    HIGH = 2


class MopRoute(IntEnum):
    S_PATTERN = 0
    Y_PATTERN = 1


class ShakeShift(IntEnum):
    LOW = 1
    MID = 2
    HIGH = 3


class Direction(IntEnum):
    FORWARD = 1
    LEFT = 2
    RIGHT = 3
    BACK = 4
    STOP = 5
    EXIT = 10


class Consumable(IntEnum):
    MAIN_BRUSH = 1
    SIDE_BRUSH = 2
    HYPA = 3
    CLOTH = 4


class DoorState(IntEnum):
    NONE = 0
    DUST_BOX = 1
    WATER_BOX = 2
    TWO_IN_ONE = 3


class ClothState(IntEnum):
    NONE = 0
    ATTACHED = 1


# ---------------------------------------------------------------------------
# MIoT property map — (siid, piid)
# ---------------------------------------------------------------------------

PROP = {
    # siid 2 — vacuum
    "status":           (2, 1),
    "fault":            (2, 2),
    "mode":             (2, 4),
    "sweep_type":       (2, 8),
    "suction_level":    (2, 11),
    # siid 3 — battery
    "battery":          (3, 1),
    # siid 4 — alarm
    "alarm":            (4, 1),
    "volume":           (4, 2),
    # siid 7 — sweep extended
    "repeat_clean":     (7, 1),
    "door_state":       (7, 3),
    "cloth_state":      (7, 4),
    "suction_state":    (7, 5),
    "water_state":      (7, 6),
    "mop_route":        (7, 7),
    "side_brush_life":  (7, 8),
    "side_brush_hours": (7, 9),
    "main_brush_life":  (7, 10),
    "main_brush_hours": (7, 11),
    "hypa_life":        (7, 12),
    "hypa_hours":       (7, 13),
    "mop_life":         (7, 14),
    "mop_hours":        (7, 15),
    "direction":        (7, 16),
    "timezone":         (7, 20),
    "language":         (7, 21),
    "cleaning_time":    (7, 22),
    "cleaning_area":    (7, 23),
    "dirt_recognize":   (7, 35),
    "pet_recognize":    (7, 36),
    "ai_recognize":     (7, 42),
    "carpet_booster":   (7, 44),
    "tank_shake":       (7, 48),
    "electrolysis":     (7, 49),
    "shake_shift":      (7, 50),
    # siid 9 — point / zone cleaning (piids per home.miot-spec.com for d106gl)
    "target_point":     (9, 1),
    "zone_points":      (9, 2),
    # siid 10 — map
    "map_remember":     (10, 1),
    "current_map_id":   (10, 2),
    "map_count":        (10, 3),
    # siid 12 — DND
    "dnd_enabled":      (12, 1),
    "dnd_start_hour":   (12, 2),
    "dnd_start_minute": (12, 3),
    "dnd_end_hour":     (12, 4),
    "dnd_end_minute":   (12, 5),
    # siid 15 — filter
    "filter_life":      (15, 1),
    "filter_hours":     (15, 2),
    # siid 16 — main brush (standard)
    "main_brush_life_std":  (16, 2),
    "main_brush_hours_std": (16, 1),
    # siid 17 — side brush (standard)
    "side_brush_life_std":  (17, 2),
    "side_brush_hours_std": (17, 1),
    # siid 18 — mop (standard)
    "mop_life_std":     (18, 1),
    "mop_hours_std":    (18, 2),
}

# ---------------------------------------------------------------------------
# MIoT action map — (siid, aiid)
# ---------------------------------------------------------------------------

ACTION = {
    # siid 2 — cleaning
    "start":                (2, 1),
    "stop":                 (2, 2),
    "start_sweep":          (2, 3),
    "start_sweep_mop":      (2, 5),
    "start_mop":            (2, 6),
    "start_room_sweep":     (2, 7),
    "start_vacuum_room":    (2, 11),
    # siid 3 — battery / dock
    "return_dock":          (3, 1),
    # siid 7 — sweep extended
    "reset_consumable":     (7, 1),
    "calibrate":            (7, 2),
    "set_room_clean":       (7, 3),
    "set_go_charging":      (7, 7),
    "find_me":              (7, 11),
    # siid 9 — point / zone
    "start_point_clean":    (9, 1),
    "pause_point_clean":    (9, 2),
    "start_zone_clean":     (9, 3),
    "pause_zone_clean":     (9, 4),
    # siid 10 — map
    "get_map_list":         (10, 1),
    "get_room_list":        (10, 13),
    "reset_map":            (10, 10),
    "build_new_map":        (10, 11),
    # siid 12 — DND
    "set_dnd":              (12, 1),
    # siid 15 — filter
    "reset_filter":         (15, 1),
    # siid 16 — main brush
    "reset_main_brush":     (16, 1),
    # siid 17 — side brush
    "reset_side_brush":     (17, 1),
    # siid 18 — mop
    "reset_mop":            (18, 1),
}


# ---------------------------------------------------------------------------
# Connection / property / action plumbing (shared with the air purifier)
# ---------------------------------------------------------------------------

_HELPER = MiotHelper(
    label="Vacuum",
    ip=_VACUUM_IP,
    token=_VACUUM_TOKEN,
    model=VACUUM_MODEL,
    prop_map=PROP,
    action_map=ACTION,
    error_cls=VacuumError,
    env_vars={"VACUUM_IP": _VACUUM_IP, "VACUUM_TOKEN": _VACUUM_TOKEN},
    token_var="VACUUM_TOKEN",
)


def validate_config() -> None:
    """Validate required vacuum connection env vars before device calls."""
    _HELPER.validate_config()


def get_device():
    """Returns a (cached) MiotDevice for the S20 vacuum."""
    return _HELPER.get_device()


def reset_device() -> None:
    """Drops the cached device so the next command reconnects."""
    _HELPER.invalidate()


def _ensure(device):
    """Returns the device, creating one if None."""
    return _HELPER.ensure(device)


def _get_prop(device, name: str) -> Any:
    return _HELPER.get_prop(device, name)


def _set_prop(device, name: str, value: Any) -> Any:
    return _HELPER.set_prop(device, name, value)


def _call_action(device, action_name: str, params: list[dict[str, Any]] | None = None) -> Any:
    return _HELPER.call_action(device, action_name, params)


# ---------------------------------------------------------------------------
# Status & information
# ---------------------------------------------------------------------------

def get_status(device=None) -> dict[str, Any]:
    """Reads all key vacuum properties into a single dict.

    Returns dict with keys: status, fault, mode, sweep_type, suction_level,
    battery, cleaning_time, cleaning_area, door_state, cloth_state, etc.
    Enum values are resolved to their names where possible.
    """
    device = _ensure(device)
    status_props = [
        "status", "fault", "mode", "sweep_type", "suction_level",
        "battery", "cleaning_time", "cleaning_area",
        "door_state", "cloth_state",
        "suction_state", "water_state", "mop_route",
        "repeat_clean", "carpet_booster",
        "dirt_recognize", "pet_recognize", "ai_recognize",
    ]
    result = _HELPER.get_props(device, status_props)
    _resolve_enums(result)
    return result


_ENUM_MAP: dict[str, type[IntEnum]] = {
    "status": Status,
    "mode": CleanMode,
    "sweep_type": SweepType,
    "suction_level": SuctionLevel,
    "suction_state": SuctionLevel,
    "water_state": WaterLevel,
    "mop_route": MopRoute,
    "door_state": DoorState,
    "cloth_state": ClothState,
}


def _resolve_enums(data: dict[str, Any]) -> None:
    for key, enum_cls in _ENUM_MAP.items():
        raw = data.get(key)
        if raw is None:
            continue
        try:
            data[key] = enum_cls(raw).name.replace("_", " ").title()
        except ValueError:
            pass


def get_battery(device=None) -> int:
    """Returns battery percentage (0–100)."""
    device = _ensure(device)
    return _get_prop(device, "battery")


def get_fault(device=None) -> int:
    """Returns fault code (0 = no fault)."""
    device = _ensure(device)
    return _get_prop(device, "fault")


def get_consumables(device=None) -> dict[str, Any]:
    """Returns remaining life and hours for all consumable parts."""
    device = _ensure(device)
    keys = [
        "main_brush_life", "main_brush_hours",
        "side_brush_life", "side_brush_hours",
        "hypa_life", "hypa_hours",
        "mop_life", "mop_hours",
        "filter_life", "filter_hours",
    ]
    return _HELPER.get_props(device, keys)


def get_cleaning_summary(device=None) -> dict[str, Any]:
    """Returns current cleaning time (min) and area (m²)."""
    device = _ensure(device)
    return {
        "cleaning_time_min": _get_prop(device, "cleaning_time"),
        "cleaning_area_m2": _get_prop(device, "cleaning_area"),
    }


def get_dnd_schedule(device=None) -> dict[str, Any]:
    """Returns the do-not-disturb schedule."""
    device = _ensure(device)
    return {
        "enabled": bool(_get_prop(device, "dnd_enabled")),
        "start": f"{_get_prop(device, 'dnd_start_hour'):02d}:{_get_prop(device, 'dnd_start_minute'):02d}",
        "end": f"{_get_prop(device, 'dnd_end_hour'):02d}:{_get_prop(device, 'dnd_end_minute'):02d}",
    }


# ---------------------------------------------------------------------------
# Cleaning actions
# ---------------------------------------------------------------------------

def start(device=None) -> Any:
    """Starts cleaning (uses the currently configured mode)."""
    device = _ensure(device)
    logger.info("Vacuum: start cleaning.")
    return _call_action(device, "start")


def stop(device=None) -> Any:
    """Stops cleaning."""
    device = _ensure(device)
    logger.info("Vacuum: stop.")
    return _call_action(device, "stop")


def pause(device=None) -> Any:
    """Pauses cleaning (alias for stop on this model)."""
    return stop(device)


def start_sweep(device=None) -> Any:
    """Starts dry sweep only (no mopping)."""
    device = _ensure(device)
    logger.info("Vacuum: start sweep only.")
    return _call_action(device, "start_sweep")


def start_mop(device=None) -> Any:
    """Starts mop only (no sweeping)."""
    device = _ensure(device)
    logger.info("Vacuum: start mop only.")
    return _call_action(device, "start_mop")


def start_sweep_mop(device=None) -> Any:
    """Starts sweep + mop combined."""
    device = _ensure(device)
    logger.info("Vacuum: start sweep and mop.")
    return _call_action(device, "start_sweep_mop")


def start_room_sweep(room_ids: str, device=None) -> Any:
    """Starts sweeping specific rooms.

    Args:
        room_ids: Comma-separated room ID string (e.g. "1,3,5").
        device: MiotDevice or None.
    """
    device = _ensure(device)
    logger.info("Vacuum: start room sweep — rooms %s.", room_ids)
    siid, aiid = ACTION["start_room_sweep"]
    return device.call_action_by(siid, aiid, [{"piid": 10, "value": room_ids}])


def start_vacuum_room_sweep(room_ids: str, device=None) -> Any:
    """Starts vacuum-only room sweep (no mop).

    Args:
        room_ids: Comma-separated room ID string.
        device: MiotDevice or None.
    """
    device = _ensure(device)
    logger.info("Vacuum: start vacuum room sweep — rooms %s.", room_ids)
    siid, aiid = ACTION["start_vacuum_room"]
    return device.call_action_by(siid, aiid, [{"piid": 10, "value": room_ids}])


def return_dock(device=None) -> Any:
    """Returns the vacuum to the dock to charge."""
    device = _ensure(device)
    logger.info("Vacuum: return to dock.")
    return _call_action(device, "return_dock")


# ---------------------------------------------------------------------------
# Zone / point cleaning
# ---------------------------------------------------------------------------

def start_point_clean(device=None) -> Any:
    """Starts point cleaning at the target point."""
    device = _ensure(device)
    logger.info("Vacuum: start point clean.")
    return _call_action(device, "start_point_clean")


def pause_point_clean(device=None) -> Any:
    """Pauses point cleaning."""
    device = _ensure(device)
    logger.info("Vacuum: pause point clean.")
    return _call_action(device, "pause_point_clean")


def start_zone_clean(device=None) -> Any:
    """Starts zone cleaning for the configured zones."""
    device = _ensure(device)
    logger.info("Vacuum: start zone clean.")
    return _call_action(device, "start_zone_clean")


def pause_zone_clean(device=None) -> Any:
    """Pauses zone cleaning."""
    device = _ensure(device)
    logger.info("Vacuum: pause zone clean.")
    return _call_action(device, "pause_zone_clean")


def set_target_point(x_y: str, device=None) -> Any:
    """Sets the target point for point cleaning.

    Args:
        x_y: Coordinate string (format depends on firmware, e.g. "25500,25500").
    """
    device = _ensure(device)
    logger.info("Vacuum: set target point %s.", x_y)
    return _set_prop(device, "target_point", x_y)


def set_zone_points(zones: str, device=None) -> Any:
    """Sets zone coordinates for zone cleaning.

    Args:
        zones: Zone definition string.
    """
    device = _ensure(device)
    logger.info("Vacuum: set zone points.")
    return _set_prop(device, "zone_points", zones)


# ---------------------------------------------------------------------------
# Suction / water / mop settings
# ---------------------------------------------------------------------------

def set_suction_level(level: SuctionLevel | int, device=None) -> Any:
    """Sets suction power.

    Args:
        level: SuctionLevel enum or int (0=Silent, 1=Basic, 2=Strong, 3=Full Speed).
    """
    device = _ensure(device)
    level = SuctionLevel(level)
    logger.info("Vacuum: suction → %s.", level.name)
    return _set_prop(device, "suction_level", level.value)


def set_water_level(level: WaterLevel | int, device=None) -> Any:
    """Sets mopping water output level.

    Args:
        level: WaterLevel enum or int (0=Low, 1=Mid, 2=High).
    """
    device = _ensure(device)
    level = WaterLevel(level)
    logger.info("Vacuum: water level → %s.", level.name)
    return _set_prop(device, "water_state", level.value)


def set_mop_route(route: MopRoute | int, device=None) -> Any:
    """Sets mop cleaning route pattern.

    Args:
        route: MopRoute enum or int (0=S-pattern, 1=Y-pattern).
    """
    device = _ensure(device)
    route = MopRoute(route)
    logger.info("Vacuum: mop route → %s.", route.name)
    return _set_prop(device, "mop_route", route.value)


def set_shake_shift(level: ShakeShift | int, device=None) -> Any:
    """Sets mop vibration intensity.

    Args:
        level: ShakeShift enum or int (1=Low, 2=Mid, 3=High).
    """
    device = _ensure(device)
    level = ShakeShift(level)
    logger.info("Vacuum: shake shift → %s.", level.name)
    return _set_prop(device, "shake_shift", level.value)


def set_mode(mode: CleanMode | int, device=None) -> Any:
    """Sets the cleaning mode for the next run.

    Args:
        mode: CleanMode enum or int (0=Sweep, 1=Sweep+Mop, 2=Mop, 3=Sweep then Mop).
    """
    device = _ensure(device)
    mode = CleanMode(mode)
    logger.info("Vacuum: mode → %s.", mode.name)
    return _set_prop(device, "mode", mode.value)


# ---------------------------------------------------------------------------
# Feature toggles
# ---------------------------------------------------------------------------

def set_repeat_clean(enabled: bool, device=None) -> Any:
    """Enables/disables double-pass cleaning."""
    device = _ensure(device)
    logger.info("Vacuum: repeat clean → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "repeat_clean", 1 if enabled else 0)


def set_carpet_booster(enabled: bool, device=None) -> Any:
    """Enables/disables automatic carpet suction boost."""
    device = _ensure(device)
    logger.info("Vacuum: carpet booster → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "carpet_booster", 1 if enabled else 0)


def set_dirt_recognition(enabled: bool, device=None) -> Any:
    """Enables/disables dirt detection (re-cleans dirty areas)."""
    device = _ensure(device)
    logger.info("Vacuum: dirt recognition → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "dirt_recognize", 1 if enabled else 0)


def set_pet_recognition(enabled: bool, device=None) -> Any:
    """Enables/disables pet avoidance."""
    device = _ensure(device)
    logger.info("Vacuum: pet recognition → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "pet_recognize", 1 if enabled else 0)


def set_ai_recognition(enabled: bool, device=None) -> Any:
    """Enables/disables AI obstacle recognition."""
    device = _ensure(device)
    logger.info("Vacuum: AI recognition → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "ai_recognize", 1 if enabled else 0)


def set_tank_shake(enabled: bool, device=None) -> Any:
    """Enables/disables water tank vibration for mopping."""
    device = _ensure(device)
    logger.info("Vacuum: tank shake → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "tank_shake", 1 if enabled else 0)


def set_electrolysis(enabled: bool, device=None) -> Any:
    """Enables/disables water electrolysis for sterilization."""
    device = _ensure(device)
    logger.info("Vacuum: electrolysis → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "electrolysis", 1 if enabled else 0)


# ---------------------------------------------------------------------------
# Alarm / find-me / volume
# ---------------------------------------------------------------------------

def find_me(device=None) -> Any:
    """Triggers the vacuum to play a sound so you can locate it."""
    device = _ensure(device)
    logger.info("Vacuum: find me!")
    return _call_action(device, "find_me")


def set_alarm(enabled: bool, device=None) -> Any:
    """Enables/disables the alarm sound."""
    device = _ensure(device)
    logger.info("Vacuum: alarm → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "alarm", enabled)


def set_volume(volume: int, device=None) -> Any:
    """Sets speaker volume (0–10).

    Args:
        volume: Volume level, 0 (mute) to 10 (max).
    """
    device = _ensure(device)
    volume = max(0, min(10, volume))
    logger.info("Vacuum: volume → %d.", volume)
    return _set_prop(device, "volume", volume)


def get_volume(device=None) -> int:
    """Returns current speaker volume (0–10)."""
    device = _ensure(device)
    return _get_prop(device, "volume")


# ---------------------------------------------------------------------------
# Remote control (manual driving)
# ---------------------------------------------------------------------------

def remote_control(direction: Direction | int, device=None) -> Any:
    """Sends a remote-control movement command.

    Args:
        direction: Direction enum or int
                   (1=Forward, 2=Left, 3=Right, 4=Back, 5=Stop, 10=Exit).
    """
    device = _ensure(device)
    direction = Direction(direction)
    logger.info("Vacuum: remote → %s.", direction.name)
    return _set_prop(device, "direction", direction.value)


def remote_forward(device=None) -> Any:
    """Move the vacuum one step forward in remote-control mode."""
    return remote_control(Direction.FORWARD, device)


def remote_left(device=None) -> Any:
    """Turn/move the vacuum left in remote-control mode."""
    return remote_control(Direction.LEFT, device)


def remote_right(device=None) -> Any:
    """Turn/move the vacuum right in remote-control mode."""
    return remote_control(Direction.RIGHT, device)


def remote_back(device=None) -> Any:
    """Move the vacuum backward in remote-control mode."""
    return remote_control(Direction.BACK, device)


def remote_stop(device=None) -> Any:
    """Stop movement while staying in remote-control mode."""
    return remote_control(Direction.STOP, device)


def remote_exit(device=None) -> Any:
    """Exits remote-control mode."""
    return remote_control(Direction.EXIT, device)


# ---------------------------------------------------------------------------
# Do-not-disturb schedule
# ---------------------------------------------------------------------------

def set_dnd(
    enabled: bool,
    start_hour: int = 22,
    start_minute: int = 0,
    end_hour: int = 8,
    end_minute: int = 0,
    timezone: int = 0,
    device=None,
) -> Any:
    """Configures the do-not-disturb window.

    Args:
        enabled: Turn DND on/off.
        start_hour: Hour DND starts (0–23).
        start_minute: Minute DND starts (0–59).
        end_hour: Hour DND ends (0–23).
        end_minute: Minute DND ends (0–59).
        timezone: Timezone offset.
    """
    device = _ensure(device)
    logger.info(
        "Vacuum: DND → %s (%02d:%02d – %02d:%02d).",
        "ON" if enabled else "OFF", start_hour, start_minute, end_hour, end_minute,
    )
    siid, aiid = ACTION["set_dnd"]
    return device.call_action_by(siid, aiid, [
        {"piid": 1, "value": 1 if enabled else 0},
        {"piid": 2, "value": start_hour},
        {"piid": 3, "value": start_minute},
        {"piid": 4, "value": end_hour},
        {"piid": 5, "value": end_minute},
        {"piid": 6, "value": timezone},
    ])


# ---------------------------------------------------------------------------
# Consumable resets
# ---------------------------------------------------------------------------

def reset_consumable(consumable: Consumable | int, device=None) -> Any:
    """Resets the hour counter for a consumable part.

    Args:
        consumable: Consumable enum or int (1=Main, 2=Side, 3=Hypa, 4=Cloth).
    """
    device = _ensure(device)
    consumable = Consumable(consumable)
    logger.info("Vacuum: reset consumable → %s.", consumable.name)
    siid, aiid = ACTION["reset_consumable"]
    return device.call_action_by(siid, aiid, [{"piid": 17, "value": consumable.value}])


def reset_main_brush(device=None) -> Any:
    """Resets main brush life counter."""
    device = _ensure(device)
    logger.info("Vacuum: reset main brush life.")
    return _call_action(device, "reset_main_brush")


def reset_side_brush(device=None) -> Any:
    """Resets side brush life counter."""
    device = _ensure(device)
    logger.info("Vacuum: reset side brush life.")
    return _call_action(device, "reset_side_brush")


def reset_filter(device=None) -> Any:
    """Resets filter life counter."""
    device = _ensure(device)
    logger.info("Vacuum: reset filter life.")
    return _call_action(device, "reset_filter")


def reset_mop(device=None) -> Any:
    """Resets mop life counter."""
    device = _ensure(device)
    logger.info("Vacuum: reset mop life.")
    return _call_action(device, "reset_mop")


# ---------------------------------------------------------------------------
# Map management
# ---------------------------------------------------------------------------

def get_map_list(device=None) -> Any:
    """Retrieves the list of saved maps."""
    device = _ensure(device)
    logger.info("Vacuum: get map list.")
    return _call_action(device, "get_map_list")


def get_room_list(device=None) -> Any:
    """Retrieves room IDs and names for the current map.

    Uses MIoT action get-map-room-list (siid=10, aiid=13).
    Reads the current map ID automatically, then queries rooms.

    Returns:
        The room-id-name-list string from the device (typically a JSON
        or comma-separated list of id/name pairs — format varies by firmware).

    Example usage:
        rooms = get_room_list()
        print(rooms)  # e.g. "1:Living Room,2:Kitchen,3:Bedroom"
        # then use room IDs with:
        start_room_sweep("1,3")
    """
    device = _ensure(device)
    cur_map_id = _get_prop(device, "current_map_id")
    logger.info("Vacuum: get room list for map %s.", cur_map_id)
    siid, aiid = ACTION["get_room_list"]
    return device.call_action_by(siid, aiid, [{"piid": 2, "value": cur_map_id}])


def reset_map(device=None) -> Any:
    """Resets (deletes) all saved maps."""
    device = _ensure(device)
    logger.warning("Vacuum: resetting all maps!")
    return _call_action(device, "reset_map")


def build_new_map(device=None) -> Any:
    """Starts building a new map (exploration run)."""
    device = _ensure(device)
    logger.info("Vacuum: build new map.")
    siid, aiid = ACTION["build_new_map"]
    return device.call_action_by(siid, aiid, [{"piid": 14, "value": 1}])


def set_map_memory(enabled: bool, device=None) -> Any:
    """Enables/disables map memory (saving maps between runs)."""
    device = _ensure(device)
    logger.info("Vacuum: map memory → %s.", "ON" if enabled else "OFF")
    return _set_prop(device, "map_remember", 1 if enabled else 0)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def calibrate(device=None) -> Any:
    """Runs sensor calibration."""
    device = _ensure(device)
    logger.info("Vacuum: calibrating sensors.")
    return _call_action(device, "calibrate")


# ---------------------------------------------------------------------------
# Tool registration hints for LLM orchestration
# ---------------------------------------------------------------------------

# Only these functions are marked for tool registration in brain_test.py.
TOOL_FUNCTIONS = {
    "get_status",
    "get_battery",
    "get_fault",
    "get_consumables",
    "get_cleaning_summary",
    "get_dnd_schedule",
    "start",
    "stop",
    "pause",
    "start_sweep",
    "start_mop",
    "start_sweep_mop",
    "start_room_sweep",
    "return_dock",
    "set_mode",
    "set_suction_level",
    "set_water_level",
    "set_mop_route",
    "set_repeat_clean",
    "set_carpet_booster",
    "set_dnd",
    "find_me",
    "set_volume",
    "get_volume",
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

    print(f"\n=== Battery: {get_battery(d)}% ===")

    print("\n=== Consumables ===")
    for k, v in get_consumables(d).items():
        print(f"  {k:20s}: {v}")

    print("\n=== Rooms ===")
    try:
        rooms = get_room_list(d)
        print(f"  {rooms}")
    except Exception as e:
        print(f"  Could not fetch rooms: {e}")
