"""Action contracts shared by HTTP controls, routines and the device controller."""

from dataclasses import dataclass, field

import yeelight_bulb_utils as bulbs


@dataclass(frozen=True)
class Action:
    device: str
    name: str
    handler: str
    fields: dict = field(default_factory=dict)
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def validate(self, body):
        if set(body) != {"action", *self.fields}:
            raise ValueError(f"Expected fields: {', '.join(['action', *self.fields])}.")
        for key, schema in self.fields.items():
            value = body[key]
            kind = schema["type"]
            kinds = kind if isinstance(kind, list) else [kind]
            actual = {int: "integer", bool: "boolean", str: "string"}.get(type(value))
            valid = actual in kinds and (actual != "string" or bool(value.strip()))
            if not valid or ("enum" in schema and value not in schema["enum"]):
                raise ValueError(f"Invalid '{key}'.")
            if kind == "integer" and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
                raise ValueError(f"Invalid '{key}'.")

    def parameters(self, body):
        values = list(self.args)
        for key in self.fields:
            value = body[key]
            if self.device == "lights" and key == "mode":
                value = bulbs.MODES[value]
            if self.name == "room_sweep":
                value = str(value)
            values.append(value)
        return values


def integer(lo, hi):
    return {"type": "integer", "minimum": lo, "maximum": hi}


def _descriptors():
    yield Action("lights", "mode", "_do_apply_mode", {"mode": {"type": "string", "enum": list(bulbs.MODES)}})
    yield Action("lights", "full_bright", "_do_apply_mode", args=(bulbs.MODES["max_bright"],))
    yield Action("lights", "party_toggle", "_do_party_toggle")
    yield Action("lights", "party_pattern", "_do_party_pattern", {"value": {"type": "integer", "minimum": 0}})
    yield Action("lights", "cycle_start", "_do_color_cycle_start")
    yield Action("lights", "cycle_next", "_do_color_cycle_advance", args=(1,))
    yield Action("lights", "cycle_prev", "_do_color_cycle_advance", args=(-1,))
    yield Action("lights", "brightness_up", "_do_brightness", args=(20,))
    yield Action("lights", "brightness_down", "_do_brightness", args=(-20,))
    yield Action("lights", "all_off", "_do_all_off")
    yield Action("lights", "random_color", "_do_random_color", kwargs={"each": False})
    yield Action("lights", "random_each", "_do_random_color", kwargs={"each": True})
    yield Action("lights", "undo", "_do_undo")
    for name, handler in {"sweep": "start_sweep", "mop": "start_mop", "sweep_mop": "start_sweep_mop",
                          "stop": "stop", "pause": "pause", "dock": "return_dock", "find_me": "find_me"}.items():
        yield Action("vacuum", name, handler)
    for name, handler, schema in (("suction", "set_suction_level", integer(0, 3)),
                                  ("water", "set_water_level", integer(0, 2)),
                                  ("volume", "set_volume", integer(0, 10)),
                                  ("room_sweep", "start_room_sweep", {"type": ["string", "integer"]})):
        yield Action("vacuum", name, handler, {"value": schema})
    yield Action("purifier", "toggle", "toggle")
    for name, handler, schema in (
        ("power", "set_power", {"type": "boolean"}), ("mode", "set_mode", integer(0, 3)),
        ("fan", "set_fan_level", integer(1, 3)), ("manual_level", "set_manual_level", integer(1, 3)),
        ("favorite_speed", "set_favorite_speed", integer(200, 2300)), ("screen", "set_screen_brightness", integer(0, 2)),
        ("anion", "set_anion", {"type": "boolean"}), ("child_lock", "set_child_lock", {"type": "boolean"}),
        ("buzzer", "set_buzzer", {"type": "boolean"}),
    ):
        yield Action("purifier", name, handler, {"value": schema})


ACTIONS = {(a.device, a.name): a for a in _descriptors()}


def describe(device, body):
    name = body.get("action")
    descriptor = ACTIONS.get((device, name)) if isinstance(name, str) else None
    if descriptor is None:
        raise ValueError(f"Unknown {device} action.")
    descriptor.validate(body)
    return descriptor


class LightActions:
    def apply_light(self, action, body, **properties):
        replay_safe = action.name in {"mode", "full_bright", "all_off", "party_pattern"}
        return self.run_action(getattr(self, action.handler), *action.parameters(body),
                               retry=replay_safe, **action.kwargs, **properties)

    def control_lights(self, targets, **properties):
        return self.run_action(self._do_control, targets, **properties)
