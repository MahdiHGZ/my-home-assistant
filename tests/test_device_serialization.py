"""Concurrency contracts for cached physical-device transports."""

import threading
import time
import unittest

from miot_base import MiotHelper
import yeelight_bulb_utils as ybu


class _TransportError(Exception):
    pass


class DeviceSerializationTests(unittest.TestCase):
    def test_miot_calls_on_one_helper_do_not_overlap(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        class Device:
            def get_property_by(self, _siid, _piid):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return [{"value": 1}]

        helper = MiotHelper(
            label="test", ip="192.0.2.1", token="0" * 32, model="test",
            prop_map={"value": (1, 1)}, action_map={},
            error_cls=_TransportError, env_vars={}, token_var="TOKEN",
        )
        device = Device()
        threads = [
            threading.Thread(target=helper.get_prop, args=(device, "value"))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(maximum, 1)

    def test_yeelight_actions_on_same_ip_do_not_overlap(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def action(_name, _ip):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1

        ybu._for_each_bulb({"A": "192.0.2.1", "B": "192.0.2.1"}, action)
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
