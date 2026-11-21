"""Run against the disposable Mosquitto and Home Assistant instances in CI."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from paho.mqtt import client as mqtt

from household import Household
from mqtt_bridge import Bridge, routine_id
from worker import Worker


def eventually(check, description, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if result := check():
            return result
        time.sleep(.1)
    raise TimeoutError(f"Timed out waiting for {description}")


def publish(client, topic, payload, *, retain=False):
    result = client.publish(topic, payload, qos=1, retain=retain)
    result.wait_for_publish(timeout=5)
    assert result.is_published(), topic


def main():
    observed = {}
    discovery = []
    pressed = []
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def subscribe(client, *_):
        client.subscribe([("coukab-test/observed", 1), ("homeassistant/+/+/+/config", 1)])

    def receive(_client, _userdata, message):
        if message.topic == "coukab-test/observed":
            reply = json.loads(message.payload)
            observed[reply["request"]] = reply
        elif not message.retain and message.payload:
            discovery.append((message.topic, json.loads(message.payload)["unique_id"]))

    sub.on_connect = subscribe
    sub.on_message = receive
    sub.connect("127.0.0.1")
    sub.loop_start()

    def ha_state(predicate, description, timeout=20):
        def read():
            request = uuid4().hex
            publish(sub, "coukab-test/query", request)
            try:
                reply = eventually(lambda: observed.pop(request, None), "Home Assistant query response", 2)
            except TimeoutError:
                return None
            return reply if predicate(reply) else None
        return eventually(read, description, timeout)

    with tempfile.TemporaryDirectory() as tmp:
        household = Household(Path(tmp) / "state.db")
        household.save({"name": "Evening", "steps": [{"device": "lights", "action": "all_off"}]})
        worker = Worker("mqtt-test", 4)

        def run(name, *, expected_steps):
            return household.run(name, lambda step: pressed.append(step["action"]), expected_steps=expected_steps)

        bridge = Bridge(SimpleNamespace(household=household, routine_worker=worker, run_routine=run))
        bridge.configure({"enabled": True, "sensors": ["temperature"], "routines": ["Evening"]})
        bridge.start()
        try:
            eventually(bridge.online.is_set, "broker connection")
            bridge.publish_state({"purifier": {"available": True, "temperature": 23.5}})
            initial = ha_state(
                lambda state: len(state["sensors"]) == len(state["buttons"]) == 1
                and state["sensors"][0]["state"] == "23.5"
                and state["buttons"][0]["state"] != "unavailable",
                "discovered temperature sensor and routine button", timeout=120,
            )
            sensor = initial["sensors"][0]["id"]
            button = initial["buttons"][0]["id"]
            assert initial["sensors"][0]["unit"] == "°C"

            publish(sub, "coukab-test/press", button)
            eventually(lambda: len(household.recent_runs()) >= 1, "Home Assistant button run")
            assert pressed == ["all_off"]

            # A duplicate broker client ID forces a real transport disconnect.
            # HA remains online, so the bridge has to reannounce on reconnect.
            takeover = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=bridge.node, protocol=mqtt.MQTTv5)
            disconnected = threading.Event()
            marker = len(discovery)
            original_disconnect = bridge.client.on_disconnect

            def record_disconnect(*args):
                original_disconnect(*args)
                bridge.publish_state({"purifier": {"available": True, "temperature": 24.5}})
                disconnected.set()

            bridge.client.on_disconnect = record_disconnect
            takeover.connect("127.0.0.1")
            try:
                assert disconnected.wait(10), "Broker did not disconnect the original client"
            finally:
                takeover.disconnect()
                bridge.client.on_disconnect = original_disconnect
            eventually(bridge.online.is_set, "bridge reconnect")
            expected = set(bridge.discovery(bridge.settings()))
            eventually(lambda: expected <= {topic for topic, _ in discovery[marker:]}, "replayed discovery")
            rediscovered = ha_state(
                lambda state: len(state["sensors"]) == len(state["buttons"]) == 1
                and state["sensors"][0]["state"] == "24.5"
                and state["buttons"][0]["state"] != "unavailable",
                "updated Home Assistant state after reconnect",
            )
            assert rediscovered["sensors"][0]["id"] == sensor
            assert rediscovered["buttons"][0]["id"] == button
            assert all(unique_id.startswith(bridge.node) for topic, unique_id in discovery if topic in expected)

            topic = bridge.base + "/run/" + routine_id("Evening")
            publish(sub, topic, "retained", retain=True)
            publish(sub, topic, "run-1")
            publish(sub, topic, "run-1")
            publish(sub, "coukab-test/press", button)
            eventually(lambda: len(household.recent_runs()) >= 3, "direct deduplicated run and second HA button run")
            assert pressed == ["all_off"] * 3
            assert all(run["outcomes"][0]["status"] == "done" for run in household.recent_runs())
            publish(sub, topic, b"", retain=True)

            bridge.configure({"enabled": False, "sensors": [], "routines": []})
            ha_state(lambda state: not state["sensors"] and not state["buttons"], "HA entity removal")
        finally:
            bridge.close()
            worker.stop()
            sub.disconnect()
            sub.loop_stop()


if __name__ == "__main__":
    os.environ["COUKAB_MQTT_HOST"] = "127.0.0.1"
    main()
