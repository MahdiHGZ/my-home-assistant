"""End-to-end coverage for device setup, isolated demo, and discovery updates."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, Request

import server_setup as setup


class ConfigurationTests(unittest.TestCase):
    def test_invalid_replacement_preserves_active_handles_and_hides_secrets(self):
        original = setup.camera.credentials()
        helper = setup.vacuum._HELPER
        with tempfile.TemporaryDirectory() as tmp, patch.object(helper, "_device", object()), patch.object(helper, "ip", "192.0.2.4"), patch.object(helper, "token", "1"*32), patch.object(helper, "env_vars", {}):
            file = Path(tmp) / ".env"
            old_handle = helper._device
            file.write_text("VACUUM_IP=192.0.2.8\nVACUUM_TOKEN="+"2"*32+"\nTAPO_IP=invalid\n")
            with self.assertRaises(ValueError):
                setup.reload_devices(file)
            self.assertIs(helper._device, old_handle)
            self.assertEqual(setup.camera.credentials(), original)
            file.write_text("VACUUM_IP=192.0.2.8\nVACUUM_TOKEN="+"2"*32+"\n")
            self.assertEqual(setup.reload_devices(file), ["vacuum"])
            self.assertIsNone(helper._device)
            self.assertEqual(helper.ip, "192.0.2.8")
            self.assertNotIn("2"*32, json.dumps(setup.configuration()))

    def test_advertised_service_tracks_address_changes(self):
        import threading
        from types import SimpleNamespace
        server = SimpleNamespace(server_address=("0.0.0.0",8080), stopped=threading.Event())
        addresses = iter(("192.0.2.7","192.0.2.8"))
        agent = setup.Advertisement(server, lambda:next(addresses))
        with patch.object(setup, "Zeroconf") as zc, patch.object(server.stopped,"wait") as wait:
            def tick(_):
                if wait.call_count == 2:
                    server.stopped.set()
            wait.side_effect=tick
            agent._loop()
            registered = zc.return_value.register_service.call_args_list
            self.assertEqual([c.args[0].parsed_addresses()[0] for c in registered],["192.0.2.7","192.0.2.8"])
            self.assertEqual(agent.local_url, "http://coukab.local:8080/")

    def test_failed_advertisement_closes_transport_before_retrying(self):
        import threading
        from types import SimpleNamespace
        server = SimpleNamespace(server_address=("0.0.0.0", 8080), stopped=threading.Event())
        agent = setup.Advertisement(server, lambda: "192.0.2.7")
        failed, recovered = Mock(), Mock()
        failed.register_service.side_effect = RuntimeError("Name already in use")

        with patch.object(setup, "Zeroconf", side_effect=[failed, recovered]) as zeroconf:
            def tick(_):
                failed.close.assert_called_once()
                if zeroconf.call_count == 2:
                    server.stopped.set()

            with patch.object(server.stopped, "wait", side_effect=tick):
                agent._loop()
        self.assertEqual(zeroconf.call_count, 2)
        recovered.register_service.assert_called_once()
        recovered.close.assert_called_once()


class DemoTests(unittest.TestCase):
    def test_demo_serves_shell_and_simulated_actions(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port=sock.getsockname()[1]
        base=f"http://127.0.0.1:{port}"
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(__file__).resolve().parents[1]
            proc=subprocess.Popen([sys.executable,str(root/"main.py"),"--demo","--watch-assets","--web-port",str(port)],
                                  cwd=tmp, env={**os.environ,"COUKAB_STATE_DB":str(Path(tmp)/"real.db")},
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+20
                while True:
                    try:
                        with urlopen(base+"/api/health",timeout=1): pass
                        break
                    except URLError:
                        if time.monotonic() > deadline or proc.poll() is not None: raise
                        time.sleep(.1)
                def get(path):
                    with urlopen(base+path,timeout=5) as res:return res.read()
                self.assertIn(b'Coukab',get("/"))
                self.assertTrue(json.loads(get("/api/setup"))["demo"])
                self.assertTrue(json.loads(get("/api/capabilities"))["bulbs"])
                self.assertTrue(json.loads(get("/api/dev-assets"))["revision"])
                with self.assertRaises(HTTPError) as missing:get("/unlisted-route")
                self.assertEqual(missing.exception.code,404)
                self.assertIn(b'MQTT',get("/setup"))
                req=Request(base+"/api/lights/action",data=b'{"action":"all_off"}',headers={"Content-Type":"application/json"})
                with urlopen(req) as res:self.assertTrue(json.load(res)["ok"])
                self.assertTrue(all(b["current_power"]=="off" for b in json.loads(get("/api/status"))["lights"]["bulbs"]))
                self.assertFalse((Path(tmp)/"real.db").exists())
            finally:
                proc.terminate()
                try: proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
