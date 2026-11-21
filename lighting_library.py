"""Named household lighting selections and captured scenes."""

import json

import yeelight_bulb_utils as yeelight


def list_entries(store, registered):
    with store.connection() as db:
        rows = db.execute("SELECT kind, name, data FROM lighting_library ORDER BY name").fetchall()
    entries = {"groups": [], "scenes": []}
    for row in rows:
        data = json.loads(row["data"])
        entries[row["kind"] + "s"].append({
            "name": row["name"], "bulbs": list(data),
            "missing": [name for name in data if name not in registered],
        })
    return entries


def save_entry(store, kind, name, bulbs):
    registered = yeelight.load_registered_bulbs()
    subset = {bulb: registered[bulb] for bulb in bulbs if bulb in registered}
    if len(subset) != len(bulbs):
        raise ValueError("One or more selected bulbs are no longer registered.")
    data = list(subset) if kind == "group" else {
        bulb: {key: value for key, value in state.items() if key != "ip"}
        for bulb, state in yeelight.read_state(subset).items()
    }
    with store.connection() as db:
        db.execute("INSERT INTO lighting_library VALUES(?,?,?) ON CONFLICT(kind,name) DO UPDATE SET data=excluded.data",
                   (kind, name, json.dumps(data)))
    return {"ok": True}


def delete_entry(store, kind, name):
    with store.connection() as db:
        db.execute("DELETE FROM lighting_library WHERE kind=? AND name=?", (kind, name))
    return {"ok": True}


def recall_scene(store, name):
    with store.connection() as db:
        row = db.execute("SELECT data FROM lighting_library WHERE kind='scene' AND name=?", (name,)).fetchone()
    if row is None:
        raise ValueError("Scene no longer exists.")
    state = json.loads(row["data"])
    registered = yeelight.load_registered_bulbs()
    missing = [bulb for bulb in state if bulb not in registered]
    subset = {bulb: registered[bulb] for bulb in state if bulb in registered}
    if not subset:
        raise ValueError("None of the bulbs in this scene are still registered.")
    if yeelight.is_party_running():
        yeelight.stop_party_dance()
    yeelight.save_state(subset)
    yeelight.apply_state(subset, state)
    return {"ok": True, "missing": missing}
