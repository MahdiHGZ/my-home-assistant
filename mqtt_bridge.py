"""Opt-in MQTT discovery for selected household readings and named routines."""

from functools import partial
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid

from paho.mqtt import client as mqtt

log = logging.getLogger(__name__)

# Local entity id, source device, property, display name, unit, HA device class.
SENSORS = {
    "pm25": ("purifier", "pm25", "PM2.5", "µg/m³", "pm25"),
    "temperature": ("purifier", "temperature", "Temperature", "°C", "temperature"),
    "humidity": ("purifier", "humidity", "Humidity", "%", "humidity"),
    "battery": ("vacuum", "battery", "Vacuum battery", "%", "battery"),
    "vacuum_status": ("vacuum", "status", "Vacuum status", None, None),
}


def routine_id(name):
    return "routine_" + hashlib.sha256(name.encode()).hexdigest()[:24]


class Bridge:
    def __init__(self, server):
        self.server = server
        self.store = server.household
        self.host = os.getenv("COUKAB_MQTT_HOST", "").strip()
        self.client = None
        self.online = threading.Event()
        self.changed = threading.Event()
        self.stop = threading.Event()
        self.latest = {}
        self.worker = None
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mqtt_settings (id INTEGER PRIMARY KEY, node TEXT, selection TEXT);
                CREATE TABLE IF NOT EXISTS mqtt_discovery (topic TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS mqtt_commands (id TEXT PRIMARY KEY, issued REAL);
            """)
            db.execute("INSERT OR IGNORE INTO mqtt_settings VALUES(1,?,?)",
                       ("coukab_" + uuid.uuid4().hex[:16], '{"enabled":false,"sensors":[],"routines":[]}'))
            self.node = db.execute("SELECT node FROM mqtt_settings").fetchone()[0]
        self.base = f"coukab/{self.node}"

    def settings(self):
        with self.store.connection() as db:
            data = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
        return {**data, "configured": bool(self.host), "connected": self.online.is_set(),
                "sensor_options": {key: item[2] for key, item in SENSORS.items()},
                "routine_options": [r["name"] for r in self.store.routines()], "topic": self.base}

    def configure(self, settings):
        if set(settings) != {"enabled", "sensors", "routines"} or type(settings["enabled"]) is not bool:
            raise ValueError("Expected enabled, sensors and routines.")
        if settings["enabled"] and not self.host:
            raise ValueError("Set COUKAB_MQTT_HOST on the server first.")
        options = self.settings()
        selected = {"enabled": settings["enabled"]}
        for key, allowed in (("sensors", SENSORS), ("routines", options["routine_options"])):
            values = settings[key]
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values):
                raise ValueError("Unknown MQTT selection.")
            selected[key] = sorted(set(values))
        with self.store.connection() as db:
            db.execute("UPDATE mqtt_settings SET selection=? WHERE id=1", (json.dumps(selected),))
        self.changed.set()
        return self.settings()

    def forget_routine(self, name):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
            if name in current["routines"]:
                current["routines"].remove(name)
                db.execute("UPDATE mqtt_settings SET selection=? WHERE id=1", (json.dumps(current),))
        self.changed.set()

    def start(self):
        if not self.host:
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node, protocol=mqtt.MQTTv5)
        if os.getenv("COUKAB_MQTT_USER"):
            client.username_pw_set(os.environ["COUKAB_MQTT_USER"], os.getenv("COUKAB_MQTT_PASSWORD"))
        try:
            port = int(os.getenv("COUKAB_MQTT_PORT", "1883"))
            if not 0 < port < 65536:
                raise ValueError
            if os.getenv("COUKAB_MQTT_TLS") == "1":
                client.tls_set()
        except (ValueError, OSError):
            log.error("Check MQTT port and TLS configuration")
            return
        client.will_set(self.base + "/available", "offline", qos=1, retain=True)
        client.max_queued_messages_set(128)
        client.reconnect_delay_set(1, 60)
        client.on_connect = self._connect
        client.on_disconnect = self._disconnect
        client.on_message = self._message
        self.client = client
        client.connect_async(self.host, port, keepalive=30)
        client.loop_start()
        self.worker = threading.Thread(target=self._loop, name="mqtt-discovery", daemon=True)
        self.worker.start()

    def _connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            return
        client.subscribe("homeassistant/status", qos=1)
        client.subscribe(self.base + "/run/+", options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True))
        self.online.set()
        self.changed.set()

    def _disconnect(self, *args):
        self.online.clear()

    def _message(self, client, userdata, msg):
        if msg.topic == "homeassistant/status":
            if msg.payload == b"online":
                self.changed.set()
            return
        if msg.retain or len(msg.payload) > 128 or self.stop.is_set():
            return
        try:
            identifier = msg.payload.decode("ascii")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier):
                return
            settings = self.settings()
            if settings["enabled"]:
                for name in settings["routines"]:
                    if msg.topic == self.base + "/run/" + routine_id(name):
                        self.server.routine_worker.submit(partial(self._run_once, name, identifier))
                        return
        except Exception:
            log.exception("Could not admit MQTT routine command")

    def _run_once(self, name, identifier):
        if self.stop.is_set():
            return
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            selection = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
            if not selection["enabled"] or name not in selection["routines"]:
                return
            db.execute("DELETE FROM mqtt_commands WHERE issued < ?", (time.time()-86400,))
            if db.execute("SELECT count(*) FROM mqtt_commands").fetchone()[0] >= 8192:
                return
            claim = db.execute("INSERT OR IGNORE INTO mqtt_commands VALUES(?,?)",
                               (routine_id(name) + "/" + identifier, time.time()))
            if not claim.rowcount:
                return
        self.server.run_routine(name)

    def discovery(self, selection):
        configs = {}
        if not selection["enabled"]:
            return configs
        def register(component, key, payload):
            payload.update(name=payload.get("name", key), unique_id=f"{self.node}_{key}",
                           device={"identifiers": [self.node], "name": "Coukab"},
                           availability_topic=self.base + "/available")
            configs[f"homeassistant/{component}/{self.node}/{key}/config"] = payload
        for key in selection["sensors"]:
            _, _, title, unit, kind = SENSORS[key]
            data = {"name": title, "state_topic": self.base + "/state/" + key,
                    "expire_after": 150}
            if unit:
                data.update(unit_of_measurement=unit, device_class=kind)
            register("sensor", key, data)
        for name in set(selection["routines"]) & set(selection["routine_options"]):
            key = routine_id(name)
            register("button", key, {"name": name, "command_topic": self.base + "/run/" + key,
                                     "command_template": "{{ now().strftime('%Y%m%d%H%M%S%f') }}", "qos": 1})
        return configs

    def _retained(self, topic, value):
        pub = self.client.publish(topic, value, qos=1, retain=True)
        pub.wait_for_publish(timeout=3)
        if not pub.is_published():
            raise TimeoutError("MQTT publication was not acknowledged")

    def reconcile(self):
        selection = self.settings()
        desired = self.discovery(selection)
        with self.store.connection() as db:
            previous = {row[0] for row in db.execute("SELECT topic FROM mqtt_discovery")}
            db.executemany("INSERT OR IGNORE INTO mqtt_discovery VALUES(?)", [(topic,) for topic in desired])
        for topic in previous - desired.keys():
            self._retained(topic, b"")
            key = topic.split("/")[-2]
            if key in SENSORS:
                self._retained(self.base + "/state/" + key, b"")
            with self.store.connection() as db:
                db.execute("DELETE FROM mqtt_discovery WHERE topic=?", (topic,))
        for topic, config in desired.items():
            self._retained(topic, json.dumps(config, ensure_ascii=False))
        self._retained(self.base + "/available", "online" if selection["enabled"] else "offline")
        self.publish_state(self.latest)

    def publish_state(self, snapshot):
        self.latest = snapshot
        if not self.online.is_set():
            return
        selected = self.settings()
        if selected["enabled"]:
            for key in selected["sensors"]:
                device, field, *_ = SENSORS[key]
                status = snapshot.get(device, {})
                value = status.get(field) if status.get("available") else None
                self.client.publish(self.base + "/state/" + key, "None" if value is None else str(value),
                                    qos=1, retain=True)

    def _loop(self):
        while not self.stop.is_set():
            self.changed.wait()
            self.changed.clear()
            if self.online.is_set() and not self.stop.is_set():
                try:
                    self.reconcile()
                except Exception:
                    log.warning("MQTT discovery update deferred")
                    if not self.stop.wait(3):
                        self.changed.set()

    def close(self):
        self.stop.set()
        self.changed.set()
        if self.worker:
            self.worker.join(timeout=3.2)
        if self.client:
            if self.online.is_set():
                try:
                    self._retained(self.base + "/available", "offline")
                except (RuntimeError, TimeoutError):
                    pass
            self.client.disconnect()
            self.client.loop_stop()
