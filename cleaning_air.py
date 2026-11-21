"""Persisted vacuum-to-purifier coordination with conditional restoration."""

import json
import threading
import time

import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
from xiaomi_details import PURIFIER_MODELS, accepted, identity

_ACTIVE = {vacuum.Status.SWEEPING, vacuum.Status.SWEEPING_AND_MOPPING, vacuum.Status.MOPPING}
_MODES = {"auto": int(purifier.Mode.AUTO), "sleep": int(purifier.Mode.SLEEP),
          "favorite": int(purifier.Mode.FAVORITE)}


class CleaningAir:
    def __init__(self, household, on_change=lambda: None):
        self.household = household
        self.on_change = on_change
        self.lock = threading.RLock()
        with household.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cleaning_air (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT)")
            row = db.execute("SELECT state FROM cleaning_air WHERE id=1").fetchone()
        self.state = json.loads(row["state"]) if row else {
            "enabled": False, "mode": "auto", "seconds": 300, "previous": None, "run": None,
        }
        self.availability = "waiting"

    def _save(self, **changes):
        state = {**self.state, **changes}
        with self.household.connection() as db:
            db.execute("INSERT OR REPLACE INTO cleaning_air VALUES(1, ?)", (json.dumps(state),))
        self.state = state

    def status(self):
        state = self.state
        run = state["run"]
        result = {key: state[key] for key in ("enabled", "mode", "seconds")}
        phase = "armed" if result["enabled"] else "disabled"
        if run:
            phase = "restoring" if run["finish"] is not None else "running"
            if run["finish"] is not None and time.time() < run["finish"]:
                phase = "settling"
            if run["override"] and run["finish"] is None:
                phase = "manual"
        return {**result, "phase": phase, "connection": self.availability}

    def configure(self, body):
        enabled, mode, seconds = (body.get(key) for key in ("enabled", "mode", "seconds"))
        if type(enabled) is not bool or not isinstance(mode, str) or mode not in _MODES:
            raise ValueError("Choose an available purifier mode and enable or disable the routine.")
        if type(seconds) is not int or not 0 <= seconds <= 3600:
            raise ValueError("Use a settling period from 0 to 3600 seconds.")
        with self.lock:
            if any(self.state[key] != value for key, value in
                   (("enabled", enabled), ("mode", mode), ("seconds", seconds))):
                self._save(enabled=enabled, mode=mode, seconds=seconds, previous=None)
            return self.status()

    def needs_observation(self):
        with self.lock:
            return self.state["enabled"] or self.state["run"] is not None

    @staticmethod
    def vacuum_status():
        device = vacuum.get_device()
        with vacuum._HELPER._io_lock:
            identity(device, {vacuum.VACUUM_MODEL}, fresh=False)
            raw = vacuum._HELPER.get_props(device, ["status"])["status"]
        return vacuum.Status(raw) if type(raw) is int else None

    @staticmethod
    def purifier_state():
        device = purifier.get_device()
        identity(device, PURIFIER_MODELS, fresh=False)
        result = purifier._HELPER.get_props(device, ["power", "mode"])
        if type(result["power"]) is not bool or type(result["mode"]) is not int:
            return None
        return result if result["mode"] in {int(mode) for mode in purifier.Mode} else None

    @staticmethod
    def write(name, value):
        device = purifier.get_device()
        siid, piid = purifier.PROP[name]
        payload = [{"did": name, "siid": siid, "piid": piid, "value": value}]
        return accepted(device.send("set_properties", payload, retry_count=0))

    def poll(self):
        if not self.needs_observation():
            return
        try:
            status = self.vacuum_status()
        except Exception:
            status = None
        self.observe(status, time.time())

    def observe(self, status, now):
        """Consume one fresh observation. None represents a missed vacuum read."""
        with self.lock:
            self._observe(status, now)

    def _observe(self, status, now):
        enabled = self.state["enabled"]
        run = self.state["run"]
        previous = self.state["previous"]
        cleaning = status in _ACTIVE if status is not None else None
        if not enabled and not run:
            self.availability = "waiting"
            return
        if enabled and cleaning is not None and cleaning != previous:
            if cleaning and previous is False and not run:
                run = {"before": {}, "applied": {}, "pending": None, "override": False,
                       "goal": {"power": True, "mode": _MODES[self.state["mode"]]},
                       "seconds": self.state["seconds"], "finish": None}
            self._save(previous=cleaning, run=run)
        if not run:
            self.availability = "ready" if cleaning is not None else "vacuum_offline"
            return
        if not enabled and (run["finish"] is None or run["finish"] > now):
            self._save(run={**run, "finish": now})
        elif cleaning is False and run["finish"] is None:
            self._save(run={**run, "finish": now + run["seconds"]})
        elif cleaning and run["finish"] is not None:
            self._save(run={**run, "finish": None})
        run = self.state["run"]
        if (run["finish"] is not None and now >= run["finish"] and (not enabled or cleaning is False) and
                not run["applied"] and not run["pending"]):
            self._save(run=None)
            return
        try:
            with purifier._HELPER._io_lock:
                current = self.purifier_state()
                if current is None:
                    self.availability = "purifier_offline"
                    return
                self.availability = "ready" if cleaning is not None else "vacuum_offline"
                self._advance(run, current, cleaning, not enabled or cleaning is False, now)
        except Exception:
            self.availability = "purifier_offline"

    def _advance(self, run, current, cleaning, can_finish, now):
        # A restart or lost response can leave a persisted write intent.
        # Reconcile it from the device and never send the same intent again.
        if run["pending"]:
            name, value, restoring = run["pending"]
            applied = dict(run["applied"])
            matched = current[name] == value
            if restoring:
                applied.pop(name, None)
            elif matched:
                applied[name] = value
            run = {**run, "applied": applied, "pending": None, "override": run["override"] or not matched}
            self._save(run=run)
            self.household.record_safely("cleaning_air", f"purifier/{name}", "done" if matched else "unconfirmed", 0)
            if matched:
                self.on_change()
        if not run["before"] and cleaning and run["finish"] is None:
            run = {**run, "before": current}
            self._save(run=run)
        expected = {**run["before"], **run["applied"]}
        changed = {name for name, value in expected.items() if current[name] != value}
        if changed and (not run["override"] or changed.intersection(run["applied"])):
            applied = {name: value for name, value in run["applied"].items() if name not in changed}
            run = {**run, "override": True, "applied": applied}
            self._save(run=run)
        if can_finish and run["finish"] is not None and now >= run["finish"]:
            for name in ("mode", "power"):
                if name in run["applied"] and current[name] == run["applied"][name]:
                    self._write_intent(run, name, run["before"][name], True)
                    return
            self._save(run=None)
        elif cleaning and run["finish"] is None and not run["override"]:
            for name, value in run["goal"].items():
                if current[name] != value:
                    self._write_intent(run, name, value, False)
                    return

    def _write_intent(self, run, name, value, restoring):
        self._save(run={**run, "pending": [name, value, restoring]})
        if not self.write(name, value):
            applied = dict(run["applied"])
            if restoring:
                applied.pop(name, None)
            self._save(run={**run, "pending": None, "override": True, "applied": applied})
            self.household.record_safely("cleaning_air", f"purifier/{name}", "failed", 0)
