"""Network-free tests for the web API dispatch layer.

These exercise request routing, capability reporting and error handling
without touching any real device — the stub controller records calls instead
of running them, and only the no-device error branches of the vacuum/purifier
handlers are tested.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tapo_camera_utils as tapo
import tuch_controller_utils as panel
import web_server
import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
import yeelight_bulb_utils as yeelight


class RecordingController:
    """Stand-in for main.Controller that records run_action dispatches."""

    def __init__(self):
        self.calls = []

    def state(self):
        return {"party_running": False, "color_cycle": "red"}

    def run_action(self, fn, *args, **kwargs):
        self.calls.append((fn.__name__, args, kwargs))
        return {"ok": True}

    def __getattr__(self, name):
        # Provide a named callable for every _do_* the handlers reference.
        if name.startswith("_do_"):
            def stub(*a, **k):
                return None
            stub.__name__ = name
            return stub
        raise AttributeError(name)


class SafeTests(unittest.TestCase):
    def test_safe_success_marks_available(self):
        out = web_server._safe(lambda: {"a": 1})
        self.assertEqual(out, {"a": 1, "available": True})

    def test_safe_failure_captures_error(self):
        def boom():
            raise RuntimeError("nope")
        out = web_server._safe(boom)
        self.assertFalse(out["available"])
        self.assertIn("nope", out["error"])

    def test_safe_wraps_scalar(self):
        self.assertEqual(web_server._safe(lambda: 5), {"value": 5, "available": True})


class EnumOptionTests(unittest.TestCase):
    def test_enum_options_shape(self):
        opts = web_server._enum_options(vacuum.SuctionLevel)
        self.assertEqual(opts[0], {"value": 0, "name": "Silent"})
        self.assertIn({"value": 3, "name": "Full Speed"}, opts)


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_structure(self):
        caps = web_server._capabilities()
        for key in (
            "chat", "chat_ready", "bulbs", "color_cycle", "scene_modes",
            "dance_patterns", "suction_levels", "water_levels",
            "purifier_modes", "fan_levels",
            "screen_brightness", "favorite_speed", "volume",
        ):
            self.assertIn(key, caps)
        self.assertEqual([m["key"] for m in caps["scene_modes"]], web_server._SCENE_MODE_KEYS)
        self.assertEqual(caps["color_cycle"], list(yeelight.COLOR_CYCLE))
        self.assertEqual(len(caps["purifier_modes"]), len(list(purifier.Mode)))


class LightsActionTests(unittest.TestCase):
    def setUp(self):
        self.c = RecordingController()

    def test_simple_action_maps_to_method(self):
        out = web_server._lights_action(self.c, {"action": "party_toggle"})
        self.assertEqual(self.c.calls[0][0], "_do_party_toggle")
        self.assertIn("state", out)

    def test_mode_passes_bulbmode(self):
        web_server._lights_action(self.c, {"action": "mode", "mode": "sunset"})
        name, args, _ = self.c.calls[0]
        self.assertEqual(name, "_do_apply_mode")
        self.assertIs(args[0], yeelight.MODES["sunset"])

    def test_cycle_next_passes_direction(self):
        web_server._lights_action(self.c, {"action": "cycle_next"})
        self.assertEqual(self.c.calls[0][:2], ("_do_color_cycle_advance", (1,)))

    def test_unknown_mode_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._lights_action(self.c, {"action": "mode", "mode": "bogus"})

    def test_unknown_action_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._lights_action(self.c, {"action": "frobnicate"})

    def test_missing_action_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._lights_action(self.c, {})


class LightsControlTests(unittest.TestCase):
    def setUp(self):
        self.c = RecordingController()

    def test_requires_a_field(self):
        with self.assertRaises(web_server._ApiError):
            web_server._lights_control(self.c, {"targets": "all"})

    def test_brightness_is_clamped(self):
        web_server._lights_control(self.c, {"targets": "I1", "brightness": 500})
        _, args, kwargs = self.c.calls[0]
        self.assertEqual(args[0], "I1")
        self.assertEqual(kwargs["brightness"], 100)

    def test_color_passthrough(self):
        web_server._lights_control(self.c, {"color": "#ff0000"})
        self.assertEqual(self.c.calls[0][2]["color"], "#ff0000")


class DeviceActionErrorTests(unittest.TestCase):
    def test_unknown_vacuum_action_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._vacuum_action({"action": "explode"})

    def test_unknown_purifier_action_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._purifier_action({"action": "explode"})


class VacuumRemoteOrderingTests(unittest.TestCase):
    def setUp(self):
        web_server._remote_latest_seq.clear()

    @mock.patch.object(web_server, "notify_status_changed")
    @mock.patch.object(vacuum, "remote_control")
    def test_direction_older_than_stop_is_discarded(self, remote, _notify):
        stop = web_server._vacuum_action({
            "action": "remote", "value": 5, "session": "drag-1", "seq": 2,
        })
        stale = web_server._vacuum_action({
            "action": "remote", "value": 1, "session": "drag-1", "seq": 1,
        })

        self.assertEqual(stop, {"ok": True})
        self.assertTrue(stale["ignored"])
        remote.assert_called_once_with(5)

    @mock.patch.object(web_server, "notify_status_changed")
    @mock.patch.object(vacuum, "remote_control")
    def test_new_drag_session_can_move_after_prior_stop(self, remote, _notify):
        web_server._vacuum_action({
            "action": "remote", "value": 5, "session": "drag-1", "seq": 2,
        })
        web_server._vacuum_action({
            "action": "remote", "value": 1, "session": "drag-2", "seq": 1,
        })

        self.assertEqual([c.args[0] for c in remote.call_args_list], [5, 1])

    @mock.patch.object(vacuum, "remote_control")
    def test_remote_sequence_metadata_must_be_complete(self, remote):
        with self.assertRaises(web_server._ApiError):
            web_server._vacuum_action({"action": "remote", "value": 1, "seq": 1})
        remote.assert_not_called()


class PartyPatternTests(unittest.TestCase):
    def test_action_maps_with_value(self):
        c = RecordingController()
        web_server._lights_action(c, {"action": "party_pattern", "value": 3})
        self.assertEqual(c.calls[0][:2], ("_do_party_pattern", (3,)))

    def test_missing_value_raises(self):
        with self.assertRaises(web_server._ApiError):
            web_server._lights_action(RecordingController(), {"action": "party_pattern"})

    def test_set_party_pattern_wraps_index(self):
        n = len(yeelight.DANCE_PATTERNS)
        original = yeelight.get_party_pattern()
        try:
            p = yeelight.set_party_pattern(n + 1)
            self.assertEqual(p, yeelight.DANCE_PATTERNS[1])
        finally:
            yeelight.set_party_pattern(yeelight.DANCE_PATTERNS.index(original))

    def test_undo_depth_is_int(self):
        self.assertIsInstance(yeelight.undo_depth(), int)


class CameraDeleteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._orig = tapo.MOMENTS_DIR
        tapo.MOMENTS_DIR = self.dir
        (self.dir / "2026-01-01_00-00-00.jpg").write_bytes(b"x")
        # Pin to "no password" so a developer's real .env value (loaded at
        # web_server import time) can't change these tests' behavior.
        self._orig_pass = web_server._DELETE_PASSWORD
        web_server._DELETE_PASSWORD = ""

    def tearDown(self):
        tapo.MOMENTS_DIR = self._orig
        web_server._DELETE_PASSWORD = self._orig_pass
        self._tmp.cleanup()

    def test_resolve_existing_image(self):
        p = web_server._resolve_moment("2026-01-01_00-00-00.jpg")
        self.assertTrue(p.is_file())

    def test_delete_removes_file(self):
        web_server._delete_moment("2026-01-01_00-00-00.jpg")
        self.assertFalse((self.dir / "2026-01-01_00-00-00.jpg").exists())

    def test_camera_delete_accepts_url_and_returns_summary(self):
        out = web_server._camera_delete({"image": "/moments/2026-01-01_00-00-00.jpg"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 0)
        self.assertFalse((self.dir / "2026-01-01_00-00-00.jpg").exists())

    def test_missing_file_raises_404(self):
        with self.assertRaises(web_server._ApiError) as cm:
            web_server._delete_moment("nope.jpg")
        self.assertEqual(cm.exception.status, 404)

    def test_path_traversal_rejected(self):
        for evil in ("../secret.jpg", "../../etc/passwd.jpg", "sub/../../x.jpg"):
            with self.assertRaises(web_server._ApiError):
                web_server._delete_moment(evil)

    def test_non_image_rejected(self):
        (self.dir / "note.txt").write_bytes(b"x")
        with self.assertRaises(web_server._ApiError):
            web_server._delete_moment("note.txt")

    def test_camera_delete_requires_image(self):
        with self.assertRaises(web_server._ApiError):
            web_server._camera_delete({})

    def test_password_unset_deletes_without_password(self):
        out = web_server._camera_delete({"image": "2026-01-01_00-00-00.jpg"})
        self.assertTrue(out["ok"])

    def test_password_set_missing_password_rejected(self):
        web_server._DELETE_PASSWORD = "secret"
        with self.assertRaises(web_server._ApiError) as cm:
            web_server._camera_delete({"image": "2026-01-01_00-00-00.jpg"})
        self.assertEqual(cm.exception.status, 403)
        self.assertTrue((self.dir / "2026-01-01_00-00-00.jpg").exists())

    def test_password_set_wrong_password_rejected(self):
        web_server._DELETE_PASSWORD = "secret"
        with self.assertRaises(web_server._ApiError) as cm:
            web_server._camera_delete(
                {"image": "2026-01-01_00-00-00.jpg", "password": "nope"})
        self.assertEqual(cm.exception.status, 403)
        self.assertTrue((self.dir / "2026-01-01_00-00-00.jpg").exists())

    def test_password_set_correct_password_deletes(self):
        web_server._DELETE_PASSWORD = "secret"
        out = web_server._camera_delete(
            {"image": "2026-01-01_00-00-00.jpg", "password": "secret"})
        self.assertTrue(out["ok"])
        self.assertFalse((self.dir / "2026-01-01_00-00-00.jpg").exists())

    def test_capabilities_reports_delete_protection(self):
        self.assertFalse(web_server._capabilities()["delete_protected"])
        web_server._DELETE_PASSWORD = "secret"
        self.assertTrue(web_server._capabilities()["delete_protected"])


class ParseRoomListTests(unittest.TestCase):
    def test_json_list_of_dicts(self):
        raw = '[{"id": 1, "name": "Living"}, {"roomId": 2, "roomName": "Kitchen"}]'
        self.assertEqual(web_server._parse_room_list(raw), [
            {"id": "1", "name": "Living"},
            {"id": "2", "name": "Kitchen"},
        ])

    def test_colon_pair_string(self):
        self.assertEqual(web_server._parse_room_list("1:Living, 2:Kitchen"), [
            {"id": "1", "name": "Living"},
            {"id": "2", "name": "Kitchen"},
        ])

    def test_plain_id_string(self):
        self.assertEqual(web_server._parse_room_list("1,2"), [
            {"id": "1", "name": "Room 1"},
            {"id": "2", "name": "Room 2"},
        ])

    def test_miio_out_wrapper(self):
        raw = {"code": 0, "out": [{"piid": 1, "value": '{"rooms": [{"id": 5, "name": "Bath"}]}'}]}
        self.assertEqual(web_server._parse_room_list(raw), [{"id": "5", "name": "Bath"}])

    def test_garbage_returns_empty(self):
        self.assertEqual(web_server._parse_room_list("no rooms here"), [])
        self.assertEqual(web_server._parse_room_list(None), [])
        self.assertEqual(web_server._parse_room_list(12345), [])


class PanelStatusTests(unittest.TestCase):
    """Flat panel payload built from a fabricated cached snapshot."""

    def setUp(self):
        self._saved = web_server._last_status
        web_server._last_status = {
            "lights": {
                "available": True,
                "bulbs": [
                    {"name": "I1", "current_power": "on", "current_brightness": "75"},
                    {"name": "I2", "current_power": "off", "current_brightness": "40"},
                ],
                "state": {"last_mode": "movie"},
            },
            "vacuum": {"available": True, "status": "Sweeping", "battery": 85},
            "purifier": {
                "available": True, "power": "ON", "mode": "Auto",
                "fan_level": "Low", "pm25": 12, "temperature": 24.5, "humidity": 40,
            },
            "moments": {"count": 17},
        }

    def tearDown(self):
        web_server._last_status = self._saved

    def test_flattens_cached_snapshot(self):
        out = web_server._panel_status(RecordingController())
        self.assertTrue(out["ok"])
        self.assertEqual(out["bulbs_on"], 1)
        self.assertEqual(out["bulbs_total"], 2)
        self.assertEqual(out["brightness"], 75)  # first bulb's level
        self.assertEqual(out["last_mode"], "movie")
        self.assertEqual(out["vac_status"], "Sweeping")
        self.assertEqual(out["vac_battery"], 85)
        self.assertTrue(out["pur_on"])
        self.assertEqual(out["temp"], 24)  # float coerced to int
        self.assertEqual(out["moments"], 17)
        self.assertIn(out["hour"], range(24))

    def test_offline_devices_have_defaults(self):
        web_server._last_status = {
            "lights": {"available": False, "error": "x"},
            "vacuum": {"available": False, "error": "x"},
            "purifier": {"available": False, "error": "x"},
            "moments": {"count": 0},
        }
        out = web_server._panel_status(RecordingController())
        self.assertFalse(out["lights_avail"])
        self.assertEqual(out["bulbs_total"], 0)
        self.assertIsNone(out["brightness"])
        self.assertEqual(out["vac_battery"], -1)
        self.assertFalse(out["pur_on"])
        self.assertEqual(out["pm25"], -1)


class PanelAlertTests(unittest.TestCase):
    """Server-pushed alerts for the touch panel (tuch_controller_utils)."""

    def setUp(self):
        self._saved = (panel._alert, panel._alert_seq, panel._broadcaster)
        panel.clear_alert()
        panel._alert_seq = 0
        self.pushed = []
        panel.set_broadcaster(lambda ev, data: self.pushed.append((ev, data)))

    def tearDown(self):
        panel._alert, panel._alert_seq, panel._broadcaster = self._saved

    def test_get_with_no_alert_is_id_zero(self):
        self.assertEqual(panel.get_alert(), {"ok": True, "id": 0})

    def test_send_stores_pushes_and_returns_alert(self):
        out = panel.send_alert("Dinner is ready", "alert")
        self.assertTrue(out["ok"])
        self.assertEqual(out["id"], 1)
        self.assertEqual(out["level"], "alert")
        self.assertEqual(self.pushed, [("alert", '{"id": 1}')])
        got = panel.get_alert()
        self.assertEqual(got["id"], 1)
        self.assertEqual(got["text"], "Dinner is ready")

    def test_send_increments_id_and_defaults_level(self):
        panel.send_alert("one")
        out = panel.send_alert("two", "bogus")
        self.assertEqual(out["id"], 2)
        self.assertEqual(out["level"], "info")

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            panel.send_alert("   ")

    def test_http_layer_maps_empty_to_apierror(self):
        with self.assertRaises(web_server._ApiError):
            web_server._panel_alert_send({"text": "  "})
        with self.assertRaises(web_server._ApiError):
            web_server._panel_alert_send({})

    def test_long_text_truncated(self):
        out = panel.send_alert("x" * 500)
        self.assertEqual(len(out["text"]), panel.ALERT_MAX_LEN)

    def test_render_no_alert_is_none(self):
        self.assertIsNone(panel.render_alert_rgb565(296, 150))

    def test_http_render_no_alert_404(self):
        with self.assertRaises(web_server._ApiError) as cm:
            web_server._panel_alert_rgb565(296, 150)
        self.assertEqual(cm.exception.status, 404)

    def test_render_size_and_clamp(self):
        panel.send_alert("hello panel", "info")
        data = panel.render_alert_rgb565(296, 150)
        self.assertEqual(len(data), 296 * 150 * 2)
        data = panel.render_alert_rgb565(9999, 1)
        self.assertEqual(len(data), 320 * 80 * 2)  # clamped to 320x80


class PanelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._saved = (set(panel._panel_ips), panel._last_panel_ip)

    def tearDown(self):
        panel._panel_ips, panel._last_panel_ip = self._saved

    def test_register_tracks_ip(self):
        panel.register_panel("192.168.1.50")
        self.assertIn("192.168.1.50", panel.connected_panels())

    def test_find_ip_falls_back_to_last_seen(self):
        panel.register_panel("192.168.1.51")
        # mDNS resolution fails -> fall back to the last registered IP.
        orig = panel.socket.gethostbyname
        panel.socket.gethostbyname = lambda *_: (_ for _ in ()).throw(OSError())
        try:
            self.assertEqual(panel.find_panel_ip("whatever.local"), "192.168.1.51")
        finally:
            panel.socket.gethostbyname = orig

    def test_register_ignores_empty(self):
        before = set(panel.connected_panels())
        panel.register_panel(None)
        panel.register_panel("")
        self.assertEqual(set(panel.connected_panels()), before)


class PanelStatusFlattenTests(unittest.TestCase):
    def test_flatten_uses_provided_hour(self):
        snap = {"lights": {"available": True, "bulbs": [], "state": {}},
                "vacuum": {}, "purifier": {}, "moments": {"count": 3}}
        out = panel.flatten_status(snap, hour=23)
        self.assertEqual(out["hour"], 23)
        self.assertEqual(out["moments"], 3)
        self.assertEqual(out["bulbs_total"], 0)

    def test_flatten_bulb_rgb_grid(self):
        # One CT-white bulb, one full RGB red, one off, one dimmed green, and two
        # registered names with no live entry -> 6 x "RRGGBB" in sorted order.
        bulbs = [
            {"name": "I1", "current_power": "on", "current_color_mode": 2,
             "current_color_temp": 2700, "current_brightness": 100},
            {"name": "I2", "current_power": "on", "current_color_mode": 1,
             "current_rgb": 0xFF0000, "current_brightness": 100},
            {"name": "I3", "current_power": "off"},
            {"name": "I4", "current_power": "on", "current_color_mode": 1,
             "current_rgb": 0x00FF00, "current_brightness": 50},
        ]
        snap = {"lights": {"available": True, "bulbs": bulbs,
                           "registered_names": ["I1", "I2", "I3", "I4", "I5", "I6"],
                           "state": {}},
                "vacuum": {}, "purifier": {}, "moments": {}}
        out = panel.flatten_status(snap, hour=12)
        groups = [out["bulb_rgb"][i:i + 6] for i in range(0, len(out["bulb_rgb"]), 6)]
        self.assertEqual(len(groups), 6)              # one per registered name
        self.assertEqual(groups[1], "FF0000")         # I2 full red
        self.assertEqual(groups[2], "000000")         # I3 off
        self.assertEqual(groups[3], "007F00")         # I4 green @ 50%
        self.assertEqual(groups[4], "000000")         # I5 no live entry
        self.assertEqual(groups[5], "000000")         # I6 no live entry
        # I1 is a warm CT white -> reddish, strong red channel, weaker blue.
        r1, g1, b1 = (int(groups[0][j:j + 2], 16) for j in (0, 2, 4))
        self.assertEqual(r1, 255)
        self.assertGreater(r1, b1)


class PanelThumbTests(unittest.TestCase):
    """RGB565 rendering of a moment image."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = tapo.MOMENTS_DIR
        tapo.MOMENTS_DIR = self._tmp.name
        import cv2
        import numpy as np
        img = np.zeros((24, 32, 3), dtype=np.uint8)
        img[:, :, 2] = 255  # pure red in BGR
        cv2.imwrite(str(Path(self._tmp.name) / "2026-01-01_00-00-00.jpg"), img)

    def tearDown(self):
        tapo.MOMENTS_DIR = self._saved_dir
        self._tmp.cleanup()

    def test_size_and_format(self):
        data = web_server._panel_moment_rgb565(None, 32, 24)
        self.assertEqual(len(data), 32 * 24 * 2)
        # Pure red -> RGB565 0xF800, big-endian on the wire.
        pixel = (data[0] << 8) | data[1]
        self.assertEqual(pixel & 0xF800, 0xF800)  # red channel saturated
        self.assertEqual(pixel & 0x07E0, 0)       # no green

    def test_dimensions_clamped(self):
        data = web_server._panel_moment_rgb565(None, 9999, 1)
        self.assertEqual(len(data), 320 * 16 * 2)

    def test_no_moments_raises_404(self):
        for f in Path(self._tmp.name).iterdir():
            f.unlink()
        with self.assertRaises(web_server._ApiError) as ctx:
            web_server._panel_moment_rgb565(None, 16, 16)
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
