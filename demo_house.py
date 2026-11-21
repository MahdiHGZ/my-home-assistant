"""Deterministic in-memory device transports for the loopback-only demo."""

import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import cv2
import numpy as np

import tapo_camera_utils as tapo
import xiaomi_airpurifier_utils as air
import xiaomi_vacuum_utils as vac
import yeelight_bulb_utils as lights


class DemoBulb:
    def __init__(self, ip):
        self.props = dict(power="on", bright="68", ct="3500", color_mode="2", rgb="16753920",
                          hue="30", sat="90")

    def get_properties(self, *args):
        return self.props.copy()

    def turn_on(self):
        self.props["power"] = "on"

    def turn_off(self):
        self.props["power"] = "off"

    def set_rgb(self, r, g, b):
        self.props.update(rgb=str(r << 16 | g << 8 | b), color_mode="1")

    def set_color_temp(self, kelvin):
        self.props.update(ct=str(kelvin), color_mode="2")

    def set_hsv(self, hue, sat):
        self.props.update(hue=str(hue), sat=str(sat), color_mode="3")

    def set_brightness(self, value):
        self.props["bright"] = str(value)

    def set_default(self):
        pass

    start_music = stop_music = set_default


class DemoMiot:
    def __init__(self, adapter, initial):
        self.adapter = adapter
        self.fields = {v: k for k, v in adapter.PROP.items()}
        self.actions = {v: k for k, v in adapter.ACTION.items()}
        self.values = initial

    def info(self, skip_cache=False):
        return SimpleNamespace(model=self.adapter._HELPER.model, firmware_version="demo-1",
                               mac_address="02:00:00:00:00:01")

    def get_properties(self, params, **kwargs):
        rows = []
        for p in params:
            name = self.fields.get((p["siid"], p["piid"]))
            rows.append(dict(did=p["did"], value=self.values.get(name), code=0 if name in self.values else -1))
        return rows

    def get_property_by(self, siid, piid):
        return [{"value": self.values.get(self.fields[(siid, piid)])}]

    def set_property_by(self, siid, piid, value):
        self.values[self.fields[(siid, piid)]] = value
        return [{"code": 0}]

    def send(self, method, payload, **kwargs):
        if method == "set_properties":
            return [self.set_property_by(p["siid"], p["piid"], p["value"])[0] for p in payload]
        raise ValueError("Unsupported demo request.")

    def call_action_by(self, siid, aiid, params):
        action = self.actions[(siid, aiid)]
        if self.adapter == vac:
            if action == "set_dnd":
                self.values.update(dict(zip(("dnd_enabled", "dnd_start_hour", "dnd_start_minute",
                                             "dnd_end_hour", "dnd_end_minute", "timezone"),
                                            [item["value"] for item in params])))
            elif action.startswith("start"):
                self.values["status"] = int(vac.Status.SWEEPING)
            elif action in {"return_dock", "stop", "pause"}:
                self.values["status"] = int(vac.Status.PAUSED if action != "return_dock" else vac.Status.CHARGING)
        elif action == "toggle":
            self.values["power"] = not self.values["power"]
        return {"code": 0, "out": []}


class DemoStream:
    def isOpened(self):
        return True

    def read(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[:] = (105, 76, 40)
        cv2.putText(frame, "COUKAB DEMO", (45, 125), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        return True, frame

    def release(self):
        pass


def _local_sockets(event, args):
    if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo"}:
        address = args[0] if event == "socket.getaddrinfo" else args[1]
        host = address[0] if isinstance(address, tuple) else address
        if host in ("localhost", None) or (isinstance(host, str) and host.startswith("/")):
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise PermissionError("The demo only supports loopback connections.")


def install():
    import web_server as web
    directory = tempfile.TemporaryDirectory(prefix="coukab-demo-")
    os.chdir(directory.name)
    os.environ["COUKAB_STATE_DB"] = "demo.sqlite3"
    os.environ.pop("COUKAB_MQTT_HOST", None)
    web._DELETE_PASSWORD = ""
    web._credentials = lambda: {"browser": (), "panel": ()}
    web._chat_available = lambda: False
    web.lan_ip = lambda: "127.0.0.1"
    sys.addaudithook(_local_sockets)
    bulbs = {name: {"id": str(n), "ip": f"127.0.0.{n}"} for n, name in enumerate(("Living", "Desk", "Hall"), 2)}
    Path("bulb.json").write_text(json.dumps(bulbs))
    lights.Bulb = DemoBulb
    lights.discover_bulbs = lambda **kw: []
    for adapter, state in (
        (vac, dict(status=int(vac.Status.CHARGING), battery=94, suction_level=1, fault=0, cleaning_time=12,
                   cleaning_area=18, water_state=1, mop_route=0, repeat_clean=False, dnd_enabled=False,
                   dnd_start_hour=22, dnd_start_minute=0, dnd_end_hour=8, dnd_end_minute=0, timezone=0,
                   main_brush_life=87, side_brush_life=74, hypa_life=90)),
        (air, dict(power=True, pm25=9, temperature=22.5, humidity=44, mode=0, fan_level=1, filter_life=81,
                   filter_used_hours=190, filter_left_days=204, motor_rpm=650, child_lock=False,
                   favorite_speed=600, anion=False, buzzer=False, screen_brightness=1)),
    ):
        helper = adapter._HELPER
        device = DemoMiot(adapter, state)
        helper.get_device = lambda selected=device: selected
        helper.validate_config = lambda: None
    vac.get_room_list = lambda: [{"id": 1, "name": "Living room"}, {"id": 2, "name": "Bedroom"}]
    def stream(*args, **kwargs):
        tapo.require_capture_allowed()
        return DemoStream()
    tapo.availability = lambda: {"available": True}
    tapo.connect = stream
    tapo.get_client = lambda *args: SimpleNamespace(getPrivacyMode=lambda: {"enabled": "off"})
    tapo.capture_moment(stream())
    return directory
