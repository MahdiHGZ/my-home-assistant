"""Run against the disposable Mosquitto and Home Assistant instances in CI."""

import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

from paho.mqtt import client as mqtt

from household import Household
from mqtt_bridge import Bridge, routine_id
from worker import Worker


def eventually(check, timeout=120):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.25)
    raise TimeoutError("Timed out waiting for Home Assistant / MQTT")


def main():
    received = []
    pressed = []
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    sub.on_connect = lambda client, *_: client.subscribe("coukab-test/observed", qos=1)
    sub.on_message = lambda client, data, msg: received.append(msg.payload)
    sub.connect("127.0.0.1")
    sub.loop_start()
    with tempfile.TemporaryDirectory() as tmp:
        household = Household(Path(tmp) / "state.db")
        household.save({"name": "Evening", "steps": [{"device": "lights", "action": "all_off"}]})
        worker = Worker("mqtt-test", 4)
        def run(name):
            return household.run(name, lambda step: pressed.append(step["action"]))
        bridge = Bridge(SimpleNamespace(household=household, routine_worker=worker, run_routine=run))
        bridge.configure({"enabled": True, "sensors": ["temperature"], "routines": ["Evening"]})
        bridge.start()
        try:
            eventually(bridge.online.is_set, 20)
            # The HA automation acknowledges the numeric state of the discovered entity.
            bridge.publish_state({"purifier": {"available": True, "temperature": 23.5}})
            eventually(lambda: b"23.5" in received)
            topic = bridge.base + "/run/" + routine_id("Evening")
            sub.publish(topic, "retained", qos=1, retain=True).wait_for_publish()
            sub.publish(topic, "run-1", qos=1).wait_for_publish()
            sub.publish(topic, "run-1", qos=1).wait_for_publish()
            eventually(lambda: pressed == ["all_off"], 10)
            bridge.configure({"enabled": False, "sensors": [], "routines": []})
            eventually(lambda: not _outstanding(household), 10)
            sub.publish(topic, "disabled", qos=1).wait_for_publish()
            time.sleep(.5)
            assert pressed == ["all_off"] and household.recent_runs()[0]["outcomes"][0]["status"] == "done"
        finally:
            bridge.close()
            worker.stop()
            sub.disconnect()
            sub.loop_stop()


def _outstanding(store):
    with store.connection() as db:
        return db.execute("SELECT count(*) FROM mqtt_discovery").fetchone()[0]


if __name__ == "__main__":
    os.environ["COUKAB_MQTT_HOST"] = "127.0.0.1"
    main()
