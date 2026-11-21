"""Regression coverage for the household server's transport and worker boundaries."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import http.client
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from household import Household
from main import ActionCancelledError, Controller
from receipts import ReceiptError, Receipts
import main
import web_server as web


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = web._BoundedThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
        self.server.household = Household(Path(self.tmp.name) / "state.db")
        self.server.controller = Controller(self.server.household)
        self.addCleanup(self.server.controller.shutdown)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body=None, **headers):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            if body is None:
                conn.request("GET", path, headers=headers)
            else:
                conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json", **headers})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_duplicate_receipt_during_and_after_physical_action(self):
        started, release = threading.Event(), threading.Event()
        def toggle():
            started.set()
            release.wait(3)
        path, body = "/api/purifier/action", {"action": "toggle"}
        headers = {"Idempotency-Key": "retry-1"}
        with patch.object(web.purifier, "toggle", side_effect=toggle) as physical, ThreadPoolExecutor(1) as pool:
            first = pool.submit(self.request, path, body, **headers)
            self.assertTrue(started.wait(1))
            code, _, receipt = self.request(path, body, **headers)
            self.assertEqual(code, 202)
            url = json.loads(receipt)["status_url"]
            self.assertEqual(json.loads(self.request(url)[2])["job"]["status"], "running")
            self.assertEqual(self.request(path, {"action": "fan", "value": 1}, **headers)[0], 409)
            release.set()
            self.assertEqual(first.result()[0], 200)
            self.assertEqual(self.request(path, body, **headers)[0], 200)
            self.assertEqual(json.loads(self.request(url)[2])["job"]["status"], "done")
            physical.assert_called_once()
        actions = json.loads(self.request("/api/history")[2])["actions"]
        self.assertEqual([(a["action"], a["outcome"]) for a in actions], [("purifier/toggle", "done")])

    def test_controller_receipt_retains_final_result_after_job_history_eviction(self):
        release = threading.Event()
        controller = self.server.controller
        def slow_capture(*, flash):
            return controller.run_action(lambda: (release.wait(2), Path("shot.jpg"))[1], timeout=.02)
        with patch.object(controller, "capture", side_effect=slow_capture):
            headers = {"Idempotency-Key": "capture-1"}
            path = "/api/camera/capture"
            code, _, raw = self.request(path, {"flash": True}, **headers)
            self.assertEqual(code, 202)
            url = json.loads(raw)["status_url"]
            self.assertEqual(self.request(path, {"flash": True}, **headers)[0], 202)
            release.set()
            controller._queue.join()
            controller._jobs.clear()
            result = json.loads(self.request(url)[2])["job"]["result"]
            self.assertEqual(result["image"], "/moments/shot.jpg")
            self.assertEqual(json.loads(self.request(path, {"flash": True}, **headers)[2]), result)

    def test_basic_auth_rotation_reads_mutations_and_origins(self):
        self.assertEqual(self.request("/api/health")[0], 200)
        with patch.dict("os.environ", {"COUKAB_BROWSER_PASSWORD": "next", "COUKAB_BROWSER_PASSWORD_PREVIOUS": "old",
                                      "COUKAB_PANEL_PASSWORD": "panel-pass"}):
            self.server.credentials = web._credentials()
        for path, body in (("/", None), ("/api/events", None), ("/moments/test.jpg", None),
                           ("/api/lights/action", {"action": "all_off"})):
            self.assertEqual(self.request(path, body)[0], 401)
        for token in ("browser:next", "browser:old", "panel:panel-pass", "panel:next"):
            auth = {"Authorization": "Basic " + base64.b64encode(token.encode()).decode()}
            self.assertEqual(self.request("/api/health", **auth)[0], 401 if token == "panel:next" else 200)
        auth = {"Authorization": "Basic " + base64.b64encode(b"browser:next").decode()}
        self.assertEqual(self.request("/api/routines/delete", {"name": "a"}, Origin="https://other.invalid", **auth)[0], 403)
        self.assertEqual(self.request("/api/routines/delete", {"name": "a"}, Origin="http://%s:%d" % self.server.server_address, **auth)[0], 200)

    def test_negotiation_revalidation_and_local_edit(self):
        root = Path(self.tmp.name)
        asset = root / "app.js"
        asset.write_text("let first = 1;\n" * 300)
        with patch.object(web, "WEB_DIR", root):
            code, hdr, compressed = self.request("/static/app.js", **{"Accept-Encoding": "gzip"})
            self.assertEqual((code, hdr["Vary"], gzip.decompress(compressed)), (200, "Accept-Encoding", asset.read_bytes()))
            self.assertLess(len(compressed), asset.stat().st_size)
            tag = hdr["ETag"]
            headers = {"Accept-Encoding": "gzip", "If-None-Match": "W/" + tag}
            code, _, body = self.request("/static/app.js", **headers)
            self.assertEqual((code, body), (304, b""))
            code, hdr, plain = self.request("/static/app.js", **{"Accept-Encoding": "*;q=1, gzip;q=0"})
            self.assertEqual(plain, asset.read_bytes())
            self.assertNotEqual(tag, hdr["ETag"])
            asset.write_text("let other = 2;\n" * 300)
            code, hdr, compressed = self.request("/static/app.js", **headers)
            self.assertEqual((code, gzip.decompress(compressed)), (200, asset.read_bytes()))
            self.assertNotEqual(tag, hdr["ETag"])

    def test_readiness_distinguishes_optional_devices_worker_and_storage(self):
        with patch.object(web, "_last_status", {"purifier": {"available": False}}):
            code, _, raw = self.request("/api/ready")
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(raw)["devices"]["purifier"]["available"])
        worker = self.server.controller.lights_worker
        with patch.object(worker, "last_progress", time.monotonic() - 7200):
            self.assertEqual(self.request("/api/ready")[0], 200)
        with patch.object(worker, "active_since", time.monotonic() - 300):
            self.assertEqual(self.request("/api/ready")[0], 503)
            self.assertEqual(self.request("/api/health")[0], 200)
        with patch.object(web.shutil, "disk_usage", side_effect=OSError):
            self.assertEqual(self.request("/api/ready")[0], 503)

    def test_routines_use_http_dispatch_and_report_partial_results(self):
        steps = [{"device": "purifier", "action": "power", "value": True}, {"device": "vacuum", "action": "dock"}]
        self.assertEqual(self.request("/api/routines/save", {"name": "Night", "steps": steps})[0], 200)
        with patch.object(web.purifier, "turn_on", side_effect=OSError), patch.object(web.vacuum, "return_dock") as dock:
            code, _, data = self.request("/api/routines/run", {"name": "Night"})
            self.assertEqual(code, 200)
            self.assertEqual([s["status"] for s in json.loads(data)["steps"]], ["failed", "done"])
            dock.assert_called_once()
        self.assertEqual(len(json.loads(self.request("/api/routines")[2])["runs"]), 1)
        steps[1]["action"] = "unknown"
        self.assertEqual(self.request("/api/routines/save", {"name": "Night", "steps": steps})[0], 400)


class LifecycleTests(unittest.TestCase):
    def test_only_control_keys_are_retained_in_household_history(self):
        store = Mock()
        controller = Controller(store)
        self.addCleanup(controller.shutdown)
        controller.dispatch("KEY_A", combo=False)
        controller.dispatch("KEY_A", combo=True)
        controller._queue.join()
        store.record_safely.assert_not_called()

    def test_slow_rtsp_allows_light_change_and_flash_restores_that_state(self):
        controller = Controller()
        self.addCleanup(controller.shutdown)
        entered, ready = threading.Event(), threading.Event()
        cap = Mock()
        def connect():
            entered.set()
            ready.wait(3)
            return cap
        light = ["before"]
        def change(value):
            light[0] = value
        with (patch.object(main.tapo_camera_utils, "connect", side_effect=connect),
              patch.object(main.tapo_camera_utils, "capture_moment", return_value=Path("shot.jpg")),
              patch.object(main.yeelight_bulb_utils, "load_registered_bulbs", return_value={}),
              patch.object(main.yeelight_bulb_utils, "read_state", side_effect=lambda _: light[0]),
              patch.object(main, "_prepare_flash_lighting", side_effect=lambda _: change("flash")),
              patch.object(main.yeelight_bulb_utils, "apply_state", side_effect=lambda _, v: change(v)),
              ThreadPoolExecutor(1) as pool):
            capture = pool.submit(controller.capture, flash=True)
            self.assertTrue(entered.wait(1))
            controller.run_action(change, "updated", timeout=0.3)
            ready.set()
            self.assertEqual(capture.result(), Path("shot.jpg"))
            self.assertEqual(light, ["updated"])
            cap.release.assert_called_once()

    def test_stopping_cancels_pending_callers_and_accepts_no_further_work(self):
        controller = Controller()
        started, release = threading.Event(), threading.Event()
        def block():
            started.set()
            release.wait(2)
        late = Mock()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(controller.run_action, block)
            self.assertTrue(started.wait(1))
            pending = pool.submit(controller.run_action, late)
            deadline = time.monotonic() + 1
            while not controller._queue.qsize() and time.monotonic() < deadline:
                time.sleep(.005)
            controller.shutdown(timeout=.02)
            with self.assertRaises(ActionCancelledError):
                pending.result(timeout=.5)
            with self.assertRaises(controller.QueueFullError):
                controller.run_action(late)
            release.set()
            first.result()
        late.assert_not_called()

    def test_service_sigterm_cancels_waiters_and_supervises_idle_worker(self):
        script = textwrap.dedent("""
            import os, threading, time
            from pathlib import Path
            import main
            def keypad(device, controller, **kwargs):
                started = threading.Event()
                release = threading.Event()
                def first():
                    started.set()
                    release.wait(4)
                def second():
                    try:
                        controller.run_action(lambda: Path("executed").touch())
                    except main.ActionCancelledError:
                        Path("cancelled").touch()
                        release.set()
                threading.Thread(target=controller.run_action, args=(first,), daemon=True).start()
                started.wait(1)
                threading.Thread(target=second, daemon=True).start()
                while not controller._queue.qsize(): time.sleep(.002)
                threading.Event().wait()
            main.run_keyboard = keypad
            main.main()
        """)
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            addr = str(Path(tmp) / "notify")
            notify.bind(addr)
            notify.settimeout(8)
            env = dict(os.environ, PYTHONPATH=str(Path(main.__file__).parent), NOTIFY_SOCKET=addr,
                       WATCHDOG_USEC="300000", COUKAB_STATE_DB=str(Path(tmp) / "state.db"))
            with subprocess.Popen([sys.executable, "-c", script, "--no-web"], cwd=tmp,
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
                try:
                    self.assertEqual(notify.recv(100), b"READY=1")
                    self.assertEqual(notify.recv(100), b"WATCHDOG=1")
                    process.terminate()
                    self.assertEqual(process.wait(timeout=8), 0)
                    self.assertTrue((Path(tmp) / "cancelled").exists())
                    self.assertFalse((Path(tmp) / "executed").exists())
                finally:
                    if process.poll() is None:
                        process.kill()

    def test_receipts_bound_capacity_without_replaying_active_requests(self):
        cache = Receipts(1, 10)
        original, _ = cache.reserve("first", b"x")
        with self.assertRaises(ReceiptError):
            cache.reserve("another", b"x")
        with self.assertRaises(ReceiptError):
            cache.reserve("x" * 129, b"x")
        cache.finish(original, (502, {"error": "failure"}))
        self.assertEqual(cache.reserve("first", b"x")[1][0], 502)


class PersistenceTests(unittest.TestCase):
    def test_restart_duplicate_triggers_and_local_time_boundaries(self):
        zone = ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db"
            store = Household(path)
            step = {"device": "purifier", "action": "toggle"}
            store.save({"name": "daily", "schedule": "01:30", "steps": [step]})
            execute = Mock()
            first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
            self.assertIsNone(store.run("daily", execute, local_time=first.replace(minute=29)))
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: store.run("daily", execute, local_time=first), range(2)))
            self.assertEqual(sum(r is not None for r in results), 1)
            store = Household(path)
            self.assertIsNone(store.run("daily", execute, local_time=first.replace(fold=1)))
            self.assertTrue(store.run("daily", execute, local_time=first.replace(day=2))["complete"])
            self.assertEqual(execute.call_count, 2)

    def test_restart_marks_interrupted_steps_without_replaying_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Household(Path(tmp) / "db")
            step = {"device": "lights", "action": "all_off"}
            store.save({"name": "evening", "steps": [step, step], "schedule": "20:00"})
            now = datetime(2026, 11, 21, 20, 0)
            with self.assertRaises(KeyboardInterrupt):
                store.run("evening", Mock(side_effect=KeyboardInterrupt), local_time=now)
            store.recover()
            self.assertEqual([s["status"] for s in store.recent_runs()[0]["outcomes"]], ["unconfirmed", "skipped"])
            self.assertIsNone(store.run("evening", Mock(), local_time=now))

    def test_history_retains_recent_samples_and_bounds_action_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Household(Path(tmp) / "db")
            now = time.time()
            reading = {"available": True, "pm25": 10, "humidity": 44, "temperature": 20}
            store.sample(reading, now - 86420)
            store.sample(reading, now)
            store.sample(dict(reading, pm25=12), now)
            hist = store.history()
            self.assertEqual([h["pm25"] for h in hist["hours"]], [12])
            with store.connection() as db:
                db.executemany("INSERT INTO actions(id) VALUES(?)", [(i,) for i in range(1, 1002)])
            store.record("keypad", "KEY_KP5", "done", 1.2)
            with store.connection() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM actions").fetchone()[0], 1000)
            self.assertEqual(store.history()["actions"][0]["source"], "keypad")
