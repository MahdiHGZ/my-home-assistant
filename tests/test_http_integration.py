"""Real-socket integration tests for the bounded HTTP server."""

import http.client
import json
import socket
import threading
import time
import unittest

import web_server


class _Controller:
    def __init__(self):
        self.calls = []

    def state(self):
        return {"party_running": False, "color_cycle": "red"}

    def run_action(self, fn, *args, **kwargs):
        self.calls.append((fn.__name__, args, kwargs))
        return {"ok": True}

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
