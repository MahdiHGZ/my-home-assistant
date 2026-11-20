"""Regression coverage for physical actions, cache invalidation and event delivery."""

import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import main
import tapo_camera_utils as camera
import web_server as web
import yeelight_bulb_utils as bulbs


class BulbActionTests(unittest.TestCase):
    def test_partial_brightness_retries_only_failed_bulb_at_absolute_target(self):
        for source in ("web", "keypad"):
            with self.subTest(source=source):
                controller = main.Controller()
                devices = {ip: mock.Mock() for ip in ("first", "old", "new")}
                for device in devices.values():
                    device.get_properties.return_value = dict(power="on", bright=40, ct=3000, color_mode=2)
                devices["old"].set_brightness.side_effect = OSError("transport lost after write")
                registry = {"A": "first", "B": "old"}
                with (
                    mock.patch.object(bulbs, "_get_bulb", side_effect=devices.__getitem__),
                    mock.patch.object(bulbs, "load_registered_bulbs", side_effect=lambda: registry.copy()),
                    mock.patch.object(bulbs, "update_discover_config", side_effect=lambda: registry.update(B="new")),
                    mock.patch.object(bulbs, "_state_stack", []),
                ):
                    if source == "web":
                        web._lights_action(controller, {"action": "brightness_up"})
                    else:
                        controller.dispatch("KEY_KPPLUS", combo=False)
                        controller._queue.join()
                    for device in devices.values():
                        device.set_brightness.assert_called_once_with(60)
                    self.assertEqual(bulbs.undo_depth(), 1)
                    self.assertEqual(bulbs._state_stack[0]["B"]["bright"], 40)

    def test_flash_and_undo_require_complete_read(self):
        device = mock.Mock()
        device.get_properties.return_value = {"power": "on"}  # incomplete reply
        with (
            mock.patch.object(bulbs, "_get_bulb", return_value=device),
            mock.patch.object(camera, "connect") as connect,
            mock.patch.object(main, "_prepare_flash_lighting") as flash,
            mock.patch.object(bulbs, "_state_stack", []),
        ):
            with self.assertRaises(bulbs.BulbBatchError):
                main._capture({"A": "ip"}, flash=True)
            flash.assert_not_called()
            connect.return_value.release.assert_called_once()
            with self.assertRaises(bulbs.BulbBatchError):
                bulbs.save_state({"A": "ip"})
            bulbs.save_state({})
            self.assertEqual(bulbs.undo_depth(), 0)

    def test_assistant_writes_stop_party_and_reads_leave_it_running(self):
        events = []
        def control_light(**kwargs):
            events.append(("tool", kwargs))
        controller = main.Controller()
        controller.on_action = lambda: events.append("notify")
        with (
            mock.patch.object(web, "_controller_ref", controller),
            mock.patch.object(bulbs, "is_party_running", return_value=True),
            mock.patch.object(bulbs, "stop_party_dance", side_effect=lambda: events.append("stop")),
        ):
            tool = web._assistant_handler("light", control_light)
            tool(light_name="A")
            tool(light_name="A", power=False)
            controller._queue.join()
        self.assertEqual(events, [("tool", {"light_name": "A"}), "stop",
                                  ("tool", {"light_name": "A", "power": False}), "notify"])


class NotificationTests(unittest.TestCase):
    def test_slow_subscriber_receives_latest_status_and_alert(self):
        client = web._EventMailbox()
        with mock.patch.object(web, "_sse_clients", {client}):
            for i in range(30):
                web._sse_broadcast("status", str(i))
                web._sse_broadcast("alert", str(i))
            self.assertEqual(dict(client.get(0) for _ in range(2)), {"alert": "29", "status": "29"})
            with self.assertRaises(queue.Empty):
                client.get(0)

    def run_notifier_once(self):
        with (
            mock.patch.object(web._status_dirty, "wait", side_effect=[True, StopIteration]),
            mock.patch.object(web, "_SSE_DEBOUNCE_S", 0),
            self.assertRaises(StopIteration),
        ):
            web._notifier_loop()

    def test_poll_only_refresh_and_assistant_changes_reach_subscribers(self):
        reading = {"power": "OFF"}
        def turn_on():
            reading["power"] = "ON"
        controller = mock.Mock()
        controller.state.return_value = {}
        client = web._EventMailbox()
        with (
            mock.patch.object(web, "_controller_ref", controller),
            mock.patch.object(web, "_last_status", {"lights": {}, "vacuum": {}, "purifier": {}}),
            mock.patch.object(web, "_last_status_time", 0),
            mock.patch.object(web, "_status_futures", {}),
            mock.patch.object(web, "_sse_clients", set()),
            mock.patch.object(web, "_lights_status", return_value={}),
            mock.patch.object(web, "_vacuum_status", return_value={}),
            mock.patch.object(web, "_purifier_status", side_effect=reading.copy),
        ):
            self.assertTrue(web._panel_cached_status(controller)["_meta"]["stale"])
            self.run_notifier_once()
            self.assertEqual(web._panel_cached_status(controller)["purifier"]["power"], "OFF")
            web._sse_clients.add(client)
            web._assistant_handler("airpurifier", turn_on)()
            self.run_notifier_once()
            event, payload = client.get(0)
            self.assertEqual(event, "status")
            self.assertEqual(json.loads(payload)["purifier"]["power"], "ON")

    def test_action_during_refresh_remains_dirty_until_next_device_read(self):
        def change_mid_read():
            web.notify_status_changed()
            return {"value": 1}
        c = mock.Mock()
        with (
            mock.patch.object(web, "_last_status", None),
            mock.patch.object(web, "_status_futures", {}),
            mock.patch.object(web, "_lights_status", return_value={}),
            mock.patch.object(web, "_vacuum_status", return_value={}),
            mock.patch.object(web, "_purifier_status", side_effect=change_mid_read) as read,
        ):
            self.assertTrue(web._cached_status(c)["_meta"]["stale"])
            read.side_effect = None
            read.return_value = {"value": 2}
            refreshed = web._cached_status(c)
            self.assertEqual(refreshed["purifier"]["value"], 2)
            self.assertFalse(refreshed["_meta"]["stale"])

    def test_gallery_excludes_temporary_files_during_deletion_and_pruning(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(camera, "MOMENTS_DIR", Path(directory)):
            for i in range(64):
                (Path(directory) / f"{i}.jpg").touch()
            (Path(directory) / ".pending.tmp.jpg").touch()
            def mutate():
                web._delete_moment("0.jpg")
                with camera.moments_lock:
                    camera._prune_moments()
            worker = threading.Thread(target=mutate)
            worker.start()
            for _ in range(20):
                self.assertFalse(any(p.name.startswith(".") for p in web._moment_files()))
            worker.join()
            self.assertFalse((Path(directory) / "0.jpg").exists())
            self.assertLessEqual(len(web._moment_files()), camera._MAX_MOMENTS)
