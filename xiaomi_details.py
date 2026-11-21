"""Firmware-checked details for the locally configured Xiaomi devices."""

import time

import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
from miot_base import MiotHelper

_VACUUM_FIELDS = {"mop_route": range(2), "repeat_clean": range(2)}
_DND_FIELDS = {
    "dnd_enabled": range(2),
    "dnd_start_hour": range(24),
    "dnd_start_minute": range(60),
    "dnd_end_hour": range(24),
    "dnd_end_minute": range(60),
}
_PURIFIER_MODELS = {
    purifier.PURIFIER_MODEL: ("h", (9, 1), 65000, 65535),
    "zhimi.airp.rmb1": ("d", (9, 1), 1000, 2500),
    "zhimi.airp.rmb3": (None, (8, 1), 0, 2500),
}
PURIFIER_MODELS = frozenset(_PURIFIER_MODELS)


class DetailError(ValueError):
    """A setting could not be confirmed."""


def accepted(reply):
    items = reply if isinstance(reply, list) else [reply]
    return bool(items) and all(isinstance(item, dict) and item.get("code") == 0 for item in items)


def identity(device, expected, *, fresh=True):
    info = device.info(skip_cache=fresh)
    if info.model not in expected or not info.firmware_version:
        raise DetailError("The Xiaomi model and firmware could not be verified.")
    return info.model, info.firmware_version, str(getattr(info, "mac_address", ""))


def _reading(value, options):
    return value if type(value) is int and value in options else None


def _action_outputs(reply):
    if not accepted(reply):
        return {}
    row = reply[0] if isinstance(reply, list) else reply
    return {item.get("piid"): item.get("value") for item in row.get("out", [])
            if isinstance(item, dict)}


def _profiles(device, map_id):
    if type(map_id) is not int:
        return None
    try:
        outputs = _action_outputs(device.call_action_by(7, 5, [{"piid": 33, "value": map_id}]))
        raw = outputs.get(34)
        if not isinstance(raw, str) or not raw:
            return None
        rows = [list(map(int, row.split("_"))) for row in raw.split(",")]
        if not all(len(row) == 8 and row[0] == 1 and row[1] > 0 and row[2] in range(4)
                   and row[3] in range(4) and row[4] in range(3) and row[5] in range(2)
                   and row[6] in range(3) and row[7] in range(2) for row in rows):
            return None
        return rows if len({row[1] for row in rows}) == len(rows) else None
    except Exception:
        return None


class XiaomiDetails:
    def __init__(self):
        self.verified = set()

    @staticmethod
    def _read_vacuum(device):
        names = [*_VACUUM_FIELDS, *_DND_FIELDS, "dnd_timezone", "current_map_id"]
        return vacuum._HELPER.get_props(device, names)

    def vacuum(self):
        device = vacuum.get_device()
        with vacuum._HELPER._io_lock:
            installed = identity(device, {vacuum.VACUUM_MODEL})
            raw = self._read_vacuum(device)
            result = {
                "model": installed[0], "firmware": installed[1], "read_at": time.time(),
                "settings": {},
            }
            for name, values in {**_VACUUM_FIELDS, **_DND_FIELDS}.items():
                value = _reading(raw.get(name), values)
                result["settings"][name] = {"value": value, "writable": value is not None
                                            and (*installed, name) in self.verified}
            tz = _reading(raw.get("dnd_timezone"), range(-99999, 100000))
            result["dnd_can_verify"] = tz is not None and all(
                result["settings"][name]["value"] is not None for name in _DND_FIELDS
            )
            result["dnd_writable"] = result["dnd_can_verify"] and (*installed, "dnd") in self.verified
            rows = _profiles(device, raw.get("current_map_id"))
            result["profiles"] = rows
            result["map_id"] = raw.get("current_map_id") if rows else None
            result["profiles_writable"] = rows is not None and (
                *installed, "profiles", raw.get("current_map_id")
            ) in self.verified
            return result

    def purifier(self):
        device = purifier.get_device()
        with purifier._HELPER._io_lock:
            model, firmware, _ = identity(device, PURIFIER_MODELS)
            used_unit, motor_address, used_max, motor_max = _PURIFIER_MODELS[model]
            fields = {
                "filter_life": ("%", 100),
                "filter_used_time": (used_unit, used_max),
                "filter_left_days": ("d", 1000),
                "motor_rpm": ("rpm", motor_max),
            }
            addresses = {name: purifier.PROP[name] for name in ("filter_life", "filter_left_days")}
            addresses["motor_rpm"] = motor_address
            if used_unit:
                addresses["filter_used_time"] = purifier.PROP["filter_used_hours"]
            helper = MiotHelper(label="Purifier", ip="", token="", model=model, prop_map=addresses,
                                action_map={}, error_cls=DetailError, env_vars={}, token_var="")
            raw = helper.get_props(device, list(addresses))
            stamp = time.time()
        return {
            "model": model, "firmware": firmware,
            "readings": {
                name: {"value": value, "unit": unit, "read_at": stamp if value is not None else None}
                for name, (unit, maximum) in fields.items()
                for value in [_reading(raw.get(name), range(maximum + 1))]
            },
        }

    def change(self, body):
        name = body.get("setting")
        probe = body.get("verify") is True
        if not isinstance(name, str) or name not in {*_VACUUM_FIELDS, "dnd", "profiles"}:
            raise DetailError("Unknown vacuum setting.")
        device = vacuum.get_device()
        with vacuum._HELPER._io_lock:
            installed = identity(device, {vacuum.VACUUM_MODEL})
            raw = self._read_vacuum(device)
            key = (*installed, name)
            if name == "profiles":
                key += (raw.get("current_map_id"),)
            if not probe and key not in self.verified:
                raise DetailError("Verify setting support on this firmware first.")
            try:
                if name in _VACUUM_FIELDS:
                    self._change_property(device, raw, name, body, probe)
                elif name == "dnd":
                    self._change_dnd(device, raw, body, probe)
                else:
                    self._change_profile(device, raw, body, probe)
            except Exception:
                self.verified.discard(key)
                raise
            self.verified.add(key)

    @staticmethod
    def _change_property(device, raw, name, body, probe):
        values = _VACUUM_FIELDS[name]
        current = _reading(raw.get(name), values)
        value = current if probe else _reading(body.get("value"), values)
        if current is None or value is None:
            raise DetailError("This setting or value is unavailable.")
        if not accepted(vacuum._HELPER.set_prop(device, name, value)):
            raise DetailError("The vacuum rejected the setting.")
        if vacuum._HELPER.get_props(device, [name])[name] != value:
            raise DetailError("The vacuum did not confirm the setting.")

    @staticmethod
    def _change_dnd(device, raw, body, probe):
        timezone = _reading(raw.get("dnd_timezone"), range(-99999, 100000))
        if timezone is None:
            raise DetailError("The vacuum did not report its DND timezone.")
        names = list(_DND_FIELDS)
        initial = [_reading(raw.get(name), values) for name, values in _DND_FIELDS.items()]
        values = initial if probe else [_reading(body.get(name), limits) for name, limits in _DND_FIELDS.items()]
        if None in initial or None in values:
            raise DetailError("The DND schedule or requested time is unavailable.")
        if not accepted(vacuum.set_dnd(*values, timezone=timezone, device=device)):
            raise DetailError("The vacuum rejected the DND schedule.")
        observed = vacuum._HELPER.get_props(device, names)
        if [observed[name] for name in names] != values:
            raise DetailError("The vacuum did not confirm the DND schedule.")

    @staticmethod
    def _change_profile(device, raw, body, probe):
        map_id = raw.get("current_map_id")
        rows = _profiles(device, map_id)
        if not rows:
            raise DetailError("The room profiles are unavailable.")
        if not probe:
            room = body.get("room")
            values = body.get("values")
            if type(room) is not int or body.get("map_id") != map_id or not isinstance(values, list) or len(values) != 4:
                raise DetailError("Reload the current room profiles.")
            limits = [range(4), range(4), range(3), range(2)]
            if any(_reading(value, limit) is None for value, limit in zip(values, limits)):
                raise DetailError("Some room profile values are invalid.")
            row = next((row for row in rows if row[1] == room), None)
            if row is None:
                raise DetailError("The room is unavailable.")
            row[2:6] = values
        encoded = ",".join("_".join(map(str, row)) for row in rows)
        reply = device.call_action_by(7, 4, [{"piid": 34, "value": encoded}, {"piid": 33, "value": map_id}])
        if not accepted(reply):
            raise DetailError("The vacuum rejected the room profiles.")
        observed = _profiles(device, map_id)
        if observed is None or sorted(observed) != sorted(rows):
            raise DetailError("The vacuum did not confirm the room profiles.")
