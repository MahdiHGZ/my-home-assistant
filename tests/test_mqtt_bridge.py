import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from household import Household
from mqtt_bridge import Bridge, _COMMAND_TTL, routine_id
from worker import Worker


class MqttInboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.household = Household(Path(directory.name) / "state.sqlite3")
        self.household.save({"name": "Evening", "steps": [{"device": "lights", "action": "all_off"}]})
        self.worker = Worker("mqtt-inbox-test", 1)
        self.addCleanup(self.worker.thread.join, 2)
        self.addCleanup(self.worker.stop)
        self.runs = []
        self.runs_ready = threading.Event()
        self.server = SimpleNamespace(household=self.household, routine_worker=self.worker,
                                      run_routine=self.run_routine)
        self.bridge = self.new_bridge()
        self.bridge.configure({"enabled": True, "sensors": [], "routines": ["Evening"]})

    def new_bridge(self):
        with patch.dict(os.environ, {"COUKAB_MQTT_HOST": "127.0.0.1"}):
            bridge = Bridge(self.server)
        self.addCleanup(bridge.close)
        return bridge

    def start_dispatcher(self):
        self.bridge.command_worker = threading.Thread(target=self.bridge._dispatch_loop, daemon=True)
        self.bridge.command_worker.start()

    def run_routine(self, name):
        self.runs.append(name)
        if len(self.runs) == 2:
            self.runs_ready.set()

    def send(self, identifier, retain=False):
        topic = self.bridge.base + "/run/" + routine_id("Evening")
        message = SimpleNamespace(topic=topic, payload=identifier.encode(), retain=retain)
        self.bridge._message(None, None, message)

    def pending_count(self):
        with self.household.connection() as db:
            return db.execute("SELECT count(*) FROM mqtt_inbox").fetchone()[0]

    def test_duplicate_presses_leave_space_and_full_worker_retries_after_restart(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def busy():
            started.set()
            release.wait(5)

        self.worker.submit(busy)
        self.assertTrue(started.wait(2))
        self.worker.submit(lambda: None)
        self.bridge.close()
        self.bridge = self.new_bridge()
        for _ in range(40):
            self.send("first")
        self.send("second")
        self.assertEqual(self.pending_count(), 2)

        self.bridge.close()
        self.bridge = self.new_bridge()
        attempted, submit = threading.Event(), self.worker.submit

        def observe_submit(*args, **kwargs):
            try:
                return submit(*args, **kwargs)
            finally:
                attempted.set()

        with patch.object(self.worker, "submit", side_effect=observe_submit):
            self.start_dispatcher()
            self.assertTrue(attempted.wait(2))
            self.assertEqual(self.pending_count(), 2)
            release.set()
            self.assertTrue(self.runs_ready.wait(3))
        self.send("first")
        self.assertEqual(self.pending_count(), 0)
        self.assertEqual(self.runs, ["Evening", "Evening"])

    def test_retained_expired_and_revoked_commands_never_run(self):
        self.send("retained", retain=True)
        self.assertEqual(self.pending_count(), 0)
        with patch("mqtt_bridge.time.time", return_value=1):
            self.send("expired")
        with self.household.connection() as db:
            command = db.execute("SELECT id, received FROM mqtt_inbox").fetchone()
        with patch("mqtt_bridge.time.time", return_value=2 + _COMMAND_TTL):
            self.bridge._run_once(*command)
            self.send("expired")
        self.assertEqual(self.pending_count(), 0)

        self.send("revoked")
        self.bridge.configure({"enabled": True, "sensors": [], "routines": []})
        self.bridge.configure({"enabled": True, "sensors": [], "routines": ["Evening"]})
        self.send("revoked")
        self.assertEqual(self.pending_count(), 0)
        self.assertEqual(self.runs, [])

    def test_inbox_rejects_excess_work_at_configured_bound(self):
        with patch("mqtt_bridge._INBOX_LIMIT", 2):
            self.send("first")
            self.send("second")
            with self.assertLogs("mqtt_bridge", level="WARNING"):
                self.send("rejected")
        self.assertEqual(self.pending_count(), 2)

    def test_deletion_revokes_sharing_even_if_name_is_reused(self):
        self.send("queued")
        self.bridge.delete_routine("Evening")
        with self.assertRaisesRegex(ValueError, "Unknown MQTT selection"):
            self.bridge.configure({"enabled": True, "sensors": [], "routines": ["Evening"]})
        self.household.save({"name": "Evening", "steps": [{"device": "lights", "action": "all_off"}]})
        self.send("new")
        self.assertEqual(self.bridge.settings()["routines"], [])
        self.assertEqual(self.pending_count(), 0)

    def test_retained_samples_and_discovery_removal_use_same_publisher(self):
        state_topic = self.bridge.base + "/state/temperature"
        state_written, release, removed = (threading.Event() for _ in range(3))
        calls = []
        result = SimpleNamespace(wait_for_publish=lambda **kwargs: None, is_published=lambda: True)

        def publish(topic, value, **kwargs):
            calls.append((topic, value, threading.current_thread()))
            if topic == state_topic and value == "23.5":
                state_written.set()
                release.wait(3)
            elif topic == self.bridge.base + "/available" and value == "online":
                if any(entry[:2] == (state_topic, b"") for entry in calls):
                    removed.set()
            return result

        self.bridge.configure({"enabled": True, "sensors": ["temperature"], "routines": []})
        self.bridge.client = SimpleNamespace(publish=publish, disconnect=lambda: None, loop_stop=lambda: None)
        self.bridge.publish_state({"purifier": {"available": True, "temperature": 23.5}})
        self.bridge.online.set()
        self.bridge.worker = threading.Thread(target=self.bridge._loop, daemon=True)
        self.bridge.worker.start()
        self.addCleanup(release.set)
        self.assertTrue(state_written.wait(2))
        self.bridge.configure({"enabled": True, "sensors": [], "routines": []})
        self.bridge.publish_state({"purifier": {"available": True, "temperature": 24.5}})
        release.set()
        self.assertTrue(removed.wait(2))
        self.assertEqual([(value, thread) for topic, value, thread in calls if topic == state_topic],
                         [("23.5", self.bridge.worker), (b"", self.bridge.worker)])
