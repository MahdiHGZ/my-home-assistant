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

from action_errors import ActionQueueFullError

log = logging.getLogger(__name__)
_COMMAND_TTL = 60
_REPLAY_TTL = 86400
_INBOX_LIMIT = 128
_REPLAY_LIMIT = 8192

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
        self._updates = threading.Condition()
        self._discovery_pending = False
        self._state_pending = False
        self.commands_ready = threading.Event()
        self.stop = threading.Event()
        self._latest = {}
        self.worker = None
        self.command_worker = None
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mqtt_settings (id INTEGER PRIMARY KEY, node TEXT, selection TEXT);
                CREATE TABLE IF NOT EXISTS mqtt_discovery (topic TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS mqtt_commands (id TEXT PRIMARY KEY, issued REAL);
                CREATE TABLE IF NOT EXISTS mqtt_inbox (
                    id TEXT PRIMARY KEY, routine TEXT NOT NULL, received REAL NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO mqtt_settings VALUES(1,?,?)",
                       ("coukab_" + uuid.uuid4().hex[:16], '{"enabled":false,"sensors":[],"routines":[]}'))
            db.execute("INSERT OR IGNORE INTO mqtt_commands SELECT id, received FROM mqtt_inbox")
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
        selected = {"enabled": settings["enabled"]}
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            routines = {row[0] for row in db.execute("SELECT name FROM routines")}
            for key, allowed in (("sensors", SENSORS), ("routines", routines)):
                values = settings[key]
                if not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values):
                    raise ValueError("Unknown MQTT selection.")
                selected[key] = sorted(set(values))
            db.execute("UPDATE mqtt_settings SET selection=? WHERE id=1", (json.dumps(selected),))
            if not selected["enabled"]:
                db.execute("DELETE FROM mqtt_inbox")
            else:
                allowed = set(selected["routines"])
                db.executemany("DELETE FROM mqtt_inbox WHERE routine=?", [
                    (row[0],) for row in db.execute("SELECT DISTINCT routine FROM mqtt_inbox")
                    if row[0] not in allowed
                ])
        self.request_reconcile()
        return self.settings()

    def delete_routine(self, name):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM routines WHERE name=?", (name,))
            current = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
            if name in current["routines"]:
                current["routines"].remove(name)
                db.execute("UPDATE mqtt_settings SET selection=? WHERE id=1", (json.dumps(current),))
            db.execute("DELETE FROM mqtt_inbox WHERE routine=?", (name,))
        self.request_reconcile()

    def request_reconcile(self):
        with self._updates:
            self._discovery_pending = True
            self._updates.notify()

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
        self.command_worker = threading.Thread(target=self._dispatch_loop, name="mqtt-commands", daemon=True)
        self.command_worker.start()

    def _connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            return
        client.subscribe("homeassistant/status", qos=1)
        client.subscribe(self.base + "/run/+", options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True))
        self.online.set()
        self.request_reconcile()

    def _disconnect(self, *args):
        self.online.clear()

    def _message(self, client, userdata, msg):
        if msg.topic == "homeassistant/status":
            if msg.payload == b"online":
                self.request_reconcile()
            return
        if msg.retain or len(msg.payload) > 128 or self.stop.is_set():
            return
        try:
            identifier = msg.payload.decode("ascii")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier):
                return
            self._admit(msg.topic, identifier)
        except Exception:
            log.exception("Could not admit MQTT routine command")

    def _admit(self, topic, identifier):
        now = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            selection = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
            if not selection["enabled"]:
                return
            for name in selection["routines"]:
                key = routine_id(name)
                if topic == self.base + "/run/" + key:
                    break
            else:
                return
            command_id = key + "/" + identifier
            db.execute("DELETE FROM mqtt_commands WHERE issued < ?", (now - _REPLAY_TTL,))
            db.execute("DELETE FROM mqtt_inbox WHERE received < ?", (now - _COMMAND_TTL,))
            if db.execute("SELECT 1 FROM mqtt_commands WHERE id=?", (command_id,)).fetchone():
                return
            pending = db.execute("SELECT count(*) FROM mqtt_inbox").fetchone()[0]
            if pending >= _INBOX_LIMIT or db.execute(
                "SELECT count(*) FROM mqtt_commands"
            ).fetchone()[0] >= _REPLAY_LIMIT:
                log.warning("MQTT routine command declined because the command inbox is full")
                return
            db.execute("INSERT INTO mqtt_inbox VALUES(?,?,?)", (command_id, name, now))
            db.execute("INSERT INTO mqtt_commands VALUES(?,?)", (command_id, now))
        self.commands_ready.set()

    def _dispatch_loop(self):
        while not self.stop.is_set():
            self.commands_ready.clear()
            try:
                with self.store.connection() as db:
                    command = db.execute("SELECT id, received FROM mqtt_inbox ORDER BY rowid LIMIT 1").fetchone()
                if not command:
                    if not self.stop.is_set():
                        self.commands_ready.wait()
                    continue
                done = threading.Event()
                self.server.routine_worker.submit(partial(self._dispatch, *command, done), cancel=done.set)
                while not self.stop.is_set() and not done.wait(.25):
                    pass
            except ActionQueueFullError:
                self.stop.wait(.25)
            except Exception:
                log.exception("Could not dispatch MQTT routine command")
                self.stop.wait(1)

    def _dispatch(self, command_id, received, done):
        try:
            self._run_once(command_id, received)
        finally:
            done.set()

    def _run_once(self, command_id, received):
        if self.stop.is_set() or self.server.routine_worker.stopping.is_set():
            return
        now = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            command = db.execute(
                "SELECT routine FROM mqtt_inbox WHERE id=? AND received=?", (command_id, received)
            ).fetchone()
            if not command:
                return
            name = command[0]
            db.execute("DELETE FROM mqtt_inbox WHERE id=?", (command_id,))
            selection = json.loads(db.execute("SELECT selection FROM mqtt_settings").fetchone()[0])
            if received < now - _COMMAND_TTL or not selection["enabled"] or name not in selection["routines"]:
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

    def _retained(self, topic, value, *, shutdown=False):
        if self.stop.is_set() and not shutdown:
            raise InterruptedError("MQTT bridge is stopping")
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
        return selection

    def publish_state(self, snapshot):
        with self._updates:
            self._latest = snapshot
            self._state_pending = True
            self._updates.notify()

    def _publish_state(self, snapshot, sensors):
        for key in sensors:
            if self.stop.is_set():
                return
            device, field, *_ = SENSORS[key]
            status = snapshot.get(device, {})
            value = status.get(field) if status.get("available") else None
            self.client.publish(self.base + "/state/" + key, "None" if value is None else str(value),
                                qos=1, retain=True)

    def _loop(self):
        sensors = []
        while True:
            with self._updates:
                self._updates.wait_for(lambda: self.stop.is_set() or (
                    self.online.is_set() and (self._discovery_pending or self._state_pending)
                ))
                if self.stop.is_set():
                    return
                reconcile = self._discovery_pending
                self._discovery_pending = self._state_pending = False
            try:
                if reconcile:
                    selection = self.reconcile()
                    sensors = selection["sensors"] if selection["enabled"] else []
                with self._updates:
                    snapshot = self._latest
                self._publish_state(snapshot, sensors)
            except Exception:
                if self.stop.is_set():
                    return
                log.warning("MQTT publication deferred", exc_info=True)
                if not self.stop.wait(3):
                    self.request_reconcile()

    def close(self):
        self.stop.set()
        with self._updates:
            self._updates.notify()
        self.commands_ready.set()
        if self.command_worker:
            self.command_worker.join(timeout=.5)
        if self.worker:
            self.worker.join(timeout=3.2)
        if self.client:
            if self.online.is_set():
                try:
                    self._retained(self.base + "/available", "offline", shutdown=True)
                except (RuntimeError, TimeoutError):
                    pass
            self.client.disconnect()
            self.client.loop_stop()
