"""Real-socket integration tests for the bounded HTTP server."""

from device_actions import LightActions
import http.client
import json
import socket
import threading
import time
import unittest
from unittest import mock

from action_errors import ActionCancelledError, ActionInProgressError, ActionQueueFullError

import web_server


class _Controller(LightActions):
    def __init__(self):
        self.calls = []

    def state(self):
        return {"party_running": False, "color_cycle": "red"}

    def run_action(self, fn, *args, **kwargs):
        self.calls.append((fn.__name__, args, kwargs))
        return {"ok": True}

    def capture(self, *, flash):
        if not flash:
            return web_server.tapo.capture_moment_for_model(include_data_uri=False)["image_path"]
        return self.run_action(self._do_capture, flash=flash)

    def job_state(self, _job_id):
        return None

    def __getattr__(self, name):
        if name.startswith("_do_"):
            def action(*_args, **_kwargs):
                return None
            action.__name__ = name
            return action
        raise AttributeError(name)


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.controller = _Controller()
        self.server = web_server._BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), web_server._Handler,
        )
        self.server.controller = self.controller
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def test_health_and_json_action_over_real_socket(self):
        status, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])

        status, payload = self.request(
            "POST", "/api/lights/action", body=b'{"action":"all_off"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        self.assertEqual(self.controller.calls[0][0], "_do_all_off")

    def test_oversized_body_receives_413_without_upload(self):
        sock = socket.create_connection((self.host, self.port), timeout=2)
        request = (
            "POST /api/chat HTTP/1.1\r\n"
            f"Host: {self.host}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {web_server._MAX_JSON_BODY_BYTES + 1}\r\n\r\n"
        )
        sock.sendall(request.encode())
        response = sock.recv(1024)
        sock.close()
        self.assertIn(b" 413 ", response)

    def test_capture_preserves_queue_outcomes_and_job_location(self):
        for exc, expected in ((ActionInProgressError("abc"), 202), (ActionQueueFullError(), 429),
                              (ActionCancelledError("abc"), 504)):
            with self.subTest(exc=exc), mock.patch.object(self.controller, "run_action", side_effect=exc):
                code, body = self.request("POST", "/api/camera/capture", '{"flash":true}',
                                          {"Content-Type": "application/json"})
                self.assertEqual(code, expected)
                if expected == 202:
                    with mock.patch.object(self.controller, "job_state", return_value={"status": "done"}):
                        status, job = self.request("GET", json.loads(body)["status_url"])
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(job)["job"]["status"], "done")

    def test_upstream_details_do_not_leak_through_json_or_stream(self):
        failure = "private device details"
        with (
            mock.patch.object(web_server.tapo, "capture_moment_for_model", side_effect=RuntimeError(failure)),
            mock.patch.object(web_server, "_resolve_moment", side_effect=web_server._ApiError(failure, 502)),
            mock.patch.object(web_server, "_chat_available", return_value=True),
            mock.patch.object(web_server, "_run_chat_stream", side_effect=RuntimeError(failure)),
        ):
            for method, path, data in (("GET", "/moments/example.jpg", None),
                                       ("POST", "/api/camera/capture", "{}"),
                                       ("POST", "/api/chat/stream", '{"message":"test"}')):
                with self.subTest(path=path):
                    status, body = self.request(method, path, data, {"Content-Type": "application/json"})
                    self.assertEqual(status, 200 if path.endswith("stream") else 502)
                    self.assertNotIn(failure.encode(), body)
                    self.assertTrue(json.loads(body)["error_id"])

    def test_stream_admission_reserves_capacity_and_replays_current_alert(self):
        with (
            mock.patch.object(web_server, "_MAX_HANDLER_THREADS", 2),
            mock.patch.object(web_server, "_cached_status", return_value={}),
            mock.patch.object(web_server.panel, "get_alert", return_value={"id": 17}),
        ):
            conn = http.client.HTTPConnection(self.host, self.port, timeout=2)
            self.addCleanup(conn.close)
            conn.request("GET", "/api/events")
            stream = conn.getresponse()
            self.addCleanup(stream.close)
            self.assertEqual(stream.status, 200)
            self.assertEqual(stream.readline(), b"event: status\n")
            stream.readline()
            stream.readline()
            self.assertEqual(stream.readline(), b"event: alert\n")
            self.assertIn(b"17", stream.readline())
            self.assertEqual(self.request("GET", "/api/events")[0], 503)
            self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_slow_partial_body_is_closed_at_deadline(self):
        original = web_server._CONNECTION_TIMEOUT_S
        web_server._CONNECTION_TIMEOUT_S = 0.1
        try:
            # setup() reads the timeout when this new connection is accepted.
            sock = socket.create_connection((self.host, self.port), timeout=2)
            sock.sendall(
                b"POST /api/chat HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 20\r\n\r\n{"
            )
            response = sock.recv(2048)
            sock.close()
        finally:
            web_server._CONNECTION_TIMEOUT_S = original
        self.assertIn(b" 408 ", response)

    def test_full_handler_pool_returns_controlled_503(self):
        self.server._handler_slots = threading.BoundedSemaphore(1)
        blocker = socket.create_connection((self.host, self.port), timeout=2)
        blocker.sendall(
            b"POST /api/chat HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: 20\r\n\r\n{"
        )
        time.sleep(0.05)

        second = socket.create_connection((self.host, self.port), timeout=2)
        second.sendall(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response = second.recv(1024)
        second.close()
        blocker.shutdown(socket.SHUT_WR)
        blocker.recv(1024)
        blocker.close()
        self.assertIn(b" 503 ", response)


if __name__ == "__main__":
    unittest.main()
