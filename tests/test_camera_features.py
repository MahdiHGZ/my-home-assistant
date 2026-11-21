"""Offline camera regression tests for capture privacy and optional jobs."""

import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np

import camera_features as features
import camera_policy as policy
import tapo_camera_utils as tapo
import web_server


class CameraFeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory)
        self.enterContext(mock.patch.object(tapo, "MOMENTS_DIR", self.path))
        self.frame = np.zeros((120, 160, 3), np.uint8)
        self.changed = self.frame.copy()
        self.changed[10:100, 10:100] = 255
        self.on_moment = mock.Mock()
        self.on_presence = mock.Mock()
        self.jobs = features.CameraJobs(threading.Event(), self.on_moment, self.on_presence)

    def configure(self, **options):
        settings = {**policy.DEFAULT, **options}
        policy.write(self.path, settings)
        return settings

    def test_privacy_settings_and_caption_routes(self):
        server = web_server._BoundedThreadingHTTPServer(("127.0.0.1", 0), web_server._Handler)
        server.credentials = {}
        server.controller = mock.Mock()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=2)
            try:
                if body is None:
                    connection.request(method, path)
                else:
                    connection.request(method, path, json.dumps(body), {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        self.assertEqual(request("POST", "/api/camera/settings", {
            **policy.DEFAULT, "override": "private",
        })[0], 200)
        self.assertTrue(request("GET", "/api/camera/settings")[1]["private"])
        self.assertEqual(request("POST", "/api/camera/preview", {})[0], 409)
        self.assertEqual(request("GET", "/api/camera/digest")[0], 409)
        name = "legacy.jpg"
        tapo.cv2.imwrite(str(self.path / name), self.frame)
        self.assertEqual(request("POST", "/api/camera/caption", {"name": name, "caption": "نور"})[0], 200)
        self.assertEqual(request("GET", "/api/camera/search?q=%D9%86%D9%88%D8%B1")[1]["matches"],
                         [{"name": name, "caption": "نور"}])

    def test_local_intervals_and_override(self):
        settings = self.configure(windows=[["22:00", "06:00"], ["12:30", "13:00"]])
        for hour, minute, private in [(22, 0, True), (1, 59, True), (6, 0, False), (12, 45, True)]:
            self.assertEqual(policy.is_private(settings, datetime(2026, 1, 1, hour, minute)), private)
        self.assertFalse(policy.is_private({**settings, "override": "allow"}, datetime(2026, 1, 1, 23)))

    def test_privacy_denies_all_entry_points_before_connecting(self):
        self.configure(override="private")
        with mock.patch.object(tapo, "_require_dependencies"), mock.patch.object(tapo, "get_client") as client:
            for operation in (tapo.connect, tapo.capture_moment_for_model, tapo.look_around, features.preview):
                with self.assertRaises(tapo.CameraPrivateError):
                    operation()
        client.assert_not_called()

    def test_corrupt_settings_and_unknown_camera_state_fail_closed(self):
        client = mock.Mock()
        client.getPrivacyMode.return_value = {}
        with self.assertRaises(tapo.CameraPrivateError):
            tapo.ensure_camera_on(client)
        client.setPrivacyMode.assert_not_called()
        for damaged_settings in ("broken", "{}"):
            (self.path / ".camera-settings.json").write_text(damaged_settings, encoding="utf-8")
            with self.assertRaises(tapo.CameraPrivateError):
                tapo.require_capture_allowed()

    def test_overlapping_captures_drop_frames_if_policy_changes(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        errors = []

        def read():
            entered.wait(timeout=5)
            release.wait(timeout=5)
            return True, self.frame

        cap = mock.Mock()
        cap.read.side_effect = read

        def capture():
            try:
                tapo.capture_moment(cap)
            except tapo.CameraPrivateError:
                errors.append("private")

        threads = [threading.Thread(target=capture) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            entered.wait(timeout=5)
            self.configure(override="private")
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=5)
        self.assertEqual(errors, ["private", "private"])
        self.assertEqual(list(self.path.glob("*.jpg")), [])

    def test_legacy_thumbnail_caption_and_delete(self):
        name = "2026-01-01_01-02-03.jpg"
        original = self.path / name
        tapo.cv2.imwrite(str(original), np.zeros((320, 640, 3), np.uint8))
        with tapo.moments_lock:
            thumb = web_server._resolve_moment(name, thumb=True)
            features.set_caption(original, "نور · Daylight")
        self.assertEqual(tapo.cv2.imread(str(thumb)).shape[:2], (128, 256))
        self.assertEqual(web_server._resolve_moment(name), original)
        self.assertEqual(features.gallery_search([original], "daylight نور")[0]["name"], name)
        self.assertEqual(features.daily_digest([original], {
            "actions": [{"ts": original.stat().st_mtime, "action": "camera/capture"}],
        })["device_actions"], {"camera": 1})
        web_server._delete_moment(name)
        self.assertFalse(thumb.exists())
        self.assertFalse(tapo.moment_caption_path(original).exists())
        self.assertEqual(features.gallery_search([original], ""), [])

    def test_motion_cooldown_and_disconnect_recovery(self):
        settings = self.configure(motion=True, presence_rule="lights_on")
        frames = [self.frame, self.changed, self.frame, tapo.TapoError("offline"), self.changed, self.frame]
        with (
            mock.patch.object(features, "_fresh_frame", side_effect=frames),
            mock.patch.object(features.time, "monotonic", side_effect=(0, 2, 4, 65, 67, 69)),
            mock.patch.object(tapo, "_detect_people", side_effect=[(0, 0), (1, 0)]),
        ):
            self.assertEqual([self.jobs._tick(settings) for _ in frames], [True, True, True, False, True, True])
        events = self.jobs.status(settings)["events"]
        self.assertEqual([(e["kind"], e["outcome"]) for e in events], [
            ("person", "applied"), ("unclassified", "notified"),
        ])
        self.assertEqual(self.on_moment.call_count, 2)
        self.on_presence.assert_called_once_with("lights_on")

    def test_preview_does_not_save_and_export_skips_missed_frame(self):
        settings = self.configure(timelapse_minutes=1)
        frames = [self.frame, self.frame, tapo.TapoError("unreadable"), self.changed]
        with (
            mock.patch.object(features, "_fresh_frame", side_effect=frames),
            mock.patch.object(features.time, "monotonic", side_effect=(0, 60, 120)),
        ):
            self.assertTrue(features.preview().startswith(b"\xff\xd8"))
            self.assertFalse(list(self.path.glob("*.jpg")))
            self.assertTrue(self.jobs._tick(settings))
            self.assertFalse(self.jobs._tick(settings))
            self.assertTrue(self.jobs._tick(settings))
        state = self.jobs.status(settings)
        self.assertEqual((state["timelapse_frames"], state["timelapse_missed"]), (2, 1))
        with zipfile.ZipFile(io.BytesIO(self.jobs.export_timelapse())) as archive:
            self.assertTrue(all(archive.read(name).startswith(b"\xff\xd8") for name in archive.namelist()))
        self.assertFalse(list(self.path.glob("*.jpg")))

    def test_unsupported_motor_and_sweep_cancellation(self):
        client = mock.Mock()
        client.getPrivacyMode.return_value = {"enabled": "off"}
        client.getMotorCapability.return_value = {"error_code": -40106}
        with mock.patch.object(tapo, "get_client", return_value=client):
            self.assertFalse(features.ptz_probe()["supported"])
            with self.assertRaises(ValueError):
                features.move_camera({"direction": "up"})
            client.moveMotorStep.assert_not_called()
            client.getMotorCapability.return_value = {"error_code": 0, "motor": {"capability": {"relative": 1}}}
            client.getPresets.return_value = {"1": "Door", "2": "Hall"}
            self.assertTrue(features.ptz_probe()["supported"])
            features.move_camera({"direction": "up"})
            client.moveMotorStep.assert_called_once_with(90)
            client.setPreset.side_effect = lambda preset: self.jobs.cancel_sweep()
            self.jobs._run_sweep(["1", "2"])
            client.setPreset.assert_called_once_with("1")
            self.assertEqual(self.jobs.status(policy.DEFAULT)["sweep"]["state"], "cancelled")
            self.assertFalse(list(self.path.glob("*.jpg")))

    def test_successful_sweep_saves_fresh_moment_for_each_preset(self):
        client = mock.Mock()
        client.getPrivacyMode.return_value = {"enabled": "off"}
        client.getMotorCapability.return_value = {"error_code": 0, "motor": {"capability": {"relative": 1}}}
        client.getPresets.return_value = {"1": "Door", "2": "Hall"}
        with (
            mock.patch.object(tapo, "get_client", return_value=client),
            mock.patch.object(features, "_fresh_frame", side_effect=[self.frame, self.changed]),
            mock.patch.object(self.jobs._sweep_cancel, "wait", return_value=False),
        ):
            self.jobs._run_sweep(["1", "2"])
        self.assertEqual(client.setPreset.call_args_list, [mock.call("1"), mock.call("2")])
        state = self.jobs.status(policy.DEFAULT)["sweep"]
        self.assertEqual(state["state"], "done")
        self.assertEqual(len(state["moments"]), 2)
        self.assertNotEqual(*state["moments"])
        self.assertEqual(self.on_moment.call_count, 2)
