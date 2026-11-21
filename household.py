"""Persistent, bounded household action history, readings and daily routines."""

from contextlib import closing, contextmanager
from datetime import datetime
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import time

from device_actions import describe


class Household:
    def __init__(self, path):
        self.path = Path(path)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY, ts REAL, source TEXT, action TEXT, outcome TEXT, duration REAL
                );
                CREATE TABLE IF NOT EXISTS air (minute INTEGER PRIMARY KEY, pm25 REAL, temperature REAL, humidity REAL);
                CREATE TABLE IF NOT EXISTS routines (
                    name TEXT PRIMARY KEY, steps TEXT, schedule TEXT
                );
                CREATE TABLE IF NOT EXISTS lighting_library (
                    kind TEXT, name TEXT, data TEXT, PRIMARY KEY(kind, name)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, name TEXT, local_date TEXT, ts REAL, outcomes TEXT,
                    UNIQUE(name, local_date)
                );
            """)

    def recover(self):
        """Called once by the service owner before accepting new routines."""
        with self.connection() as db:
            for row in db.execute("SELECT id, outcomes FROM runs"):
                steps = json.loads(row["outcomes"])
                if any(s["status"] in {"running", "pending"} for s in steps):
                    for step in steps:
                        if step["status"] == "running":
                            step["status"] = "unconfirmed"
                        elif step["status"] == "pending":
                            step["status"] = "skipped"
                    db.execute("UPDATE runs SET outcomes=? WHERE id=?", (json.dumps(steps), row["id"]))

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def record(self, source, action, outcome, duration):
        with self.connection() as db:
            db.execute("INSERT INTO actions VALUES(NULL,?,?,?,?,?)",
                       (time.time(), source, action, outcome, round(duration, 3)))
            db.execute("DELETE FROM actions WHERE id <= (SELECT MAX(id) - 1000 FROM actions)")

    def record_safely(self, source, action, outcome, duration):
        try:
            self.record(source, action, outcome, duration)
        except sqlite3.Error:
            logging.exception("Could not save action history")

    def sample(self, reading, timestamp):
        if not reading.get("available"):
            return
        def numeric(key):
            value = reading.get(key)
            return value if type(value) in (int, float) and math.isfinite(value) else None
        values = tuple(numeric(key) for key in ("pm25", "temperature", "humidity"))
        if all(value is None for value in values):
            return
        minute = int(timestamp // 60)
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO air VALUES(?,?,?,?)", (minute, *values))
            db.execute("DELETE FROM air WHERE minute NOT BETWEEN ? AND ?", (minute - 1439, minute))

    def history(self):
        with self.connection() as db:
            hours = db.execute("""
                SELECT (minute / 60) * 3600 AS ts, AVG(pm25) AS pm25, AVG(temperature) AS temperature,
                       AVG(humidity) AS humidity FROM air WHERE minute > ? GROUP BY minute / 60 ORDER BY ts
            """, (int(time.time() // 60) - 1440,))
            return {"hours": [dict(r) for r in hours], "actions": [dict(r) for r in db.execute(
                "SELECT ts,source,action,outcome,duration FROM actions ORDER BY id DESC LIMIT 100")]}

    def save(self, body):
        name = body.get("name")
        steps = body.get("steps")
        schedule = body.get("schedule")
        if schedule == "":
            schedule = None
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise ValueError("Use a routine name between 1 and 64 characters.")
        if schedule is not None and (not isinstance(schedule, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule)):
            raise ValueError("Use a daily time in HH:MM format.")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
            raise ValueError("Choose between 1 and 16 steps.")
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("device"), str):
                raise ValueError("Each step needs a device and action.")
            describe(step["device"], {k: v for k, v in step.items() if k != "device"})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM routines WHERE name != ?", (name.strip(),)).fetchone()[0] >= 32:
                raise ValueError("At most 32 routines may be saved.")
            db.execute("INSERT OR REPLACE INTO routines VALUES(?,?,?)",
                       (name.strip(), json.dumps(steps), schedule))

    def routines(self):
        with self.connection() as db:
            return [{**dict(row), "steps": json.loads(row["steps"])}
                    for row in db.execute("SELECT * FROM routines ORDER BY name")]

    def delete(self, name):
        with self.connection() as db:
            db.execute("DELETE FROM routines WHERE name=?", (name,))

    def run(self, name, execute, *, local_time: datetime | None = None):
        # Claim a scheduled local date before device I/O. An interrupted run
        # keeps its claim, including during a repeated DST hour or restart.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM routines WHERE name=?", (name,)).fetchone()
            if row is None:
                raise ValueError("No such routine.")
            if local_time and row["schedule"] != local_time.strftime("%H:%M"):
                return None
            day = local_time.date().isoformat() if local_time else None
            steps = json.loads(row["steps"])
            initial = [{"device": s["device"], "action": s["action"], "status": "pending"} for s in steps]
            db.execute("DELETE FROM runs WHERE local_date IS NOT NULL AND ts < ?", (time.time() - 7*86400,))
            cursor = db.execute("INSERT OR IGNORE INTO runs VALUES(NULL,?,?,?,?)",
                                (name, day, time.time(), json.dumps(initial)))
            if not cursor.rowcount:
                return None
            run_id = cursor.lastrowid
            db.execute("DELETE FROM runs WHERE local_date IS NULL AND outcomes NOT LIKE '%\"pending\"%' AND outcomes NOT LIKE '%\"running\"%' AND id NOT IN "
                       "(SELECT id FROM runs WHERE local_date IS NULL ORDER BY id DESC LIMIT 128)")
        for step, result in zip(steps, initial):
            result["status"] = "running"
            with self.connection() as db:
                db.execute("UPDATE runs SET outcomes=? WHERE id=?", (json.dumps(initial), run_id))
            try:
                execute(step)
                result["status"] = "done"
            except Exception:
                result["status"] = "failed"
            with self.connection() as db:
                db.execute("UPDATE runs SET outcomes=? WHERE id=?", (json.dumps(initial), run_id))
        return {"ok": True, "complete": all(s["status"] == "done" for s in initial), "steps": initial, "run_id": run_id}

    def recent_runs(self):
        with self.connection() as db:
            return [{**dict(r), "outcomes": json.loads(r["outcomes"])} for r in db.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 32")]
