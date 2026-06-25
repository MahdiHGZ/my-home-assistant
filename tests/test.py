"""Dump all information from the Xiaomi S20 vacuum: status, rooms, maps,
consumables, DND, settings, and every readable property."""

import logging

import xiaomi_vacuum_utils as vac

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("test")


def safe_read(label, func, *args):
    try:
        return func(*args)
    except Exception as e:
        log.warning("  Could not read %s: %s", label, e)
        return None


def main():
    vac.validate_config()
    d = vac.get_device()

    # ── Device info ──
    print("=" * 60)
    print("XIAOMI VACUUM S20 — FULL DEVICE DUMP")
    print("=" * 60)

    try:
        info = d.info()
        print(f"\n{'— Device Info —':^60}")
        for attr in ("model", "firmware_version", "hardware_version", "mac_address"):
            val = getattr(info, attr, None)
            if val is not None:
                print(f"  {attr:24s}: {val}")
    except Exception as e:
        log.warning("Could not read device info: %s", e)

    # ── Status ──
    print(f"\n{'— Status —':^60}")
    status = vac.get_status(d)
    for k, v in status.items():
        print(f"  {k:24s}: {v}")

    # ── Battery ──
    battery = safe_read("battery", vac.get_battery, d)
    print(f"\n{'— Battery —':^60}")
    print(f"  {'level':24s}: {battery}%")

    # ── Consumables ──
    print(f"\n{'— Consumables —':^60}")
    consumables = vac.get_consumables(d)
    for k, v in consumables.items():
        unit = "%" if "life" in k else "hours"
        print(f"  {k:24s}: {v} {unit}")

    # ── DND schedule ──
    print(f"\n{'— Do Not Disturb —':^60}")
    try:
        dnd = vac.get_dnd_schedule(d)
        for k, v in dnd.items():
            print(f"  {k:24s}: {v}")
    except Exception as e:
        print(f"  Could not read DND: {e}")

    # ── Cleaning summary ──
    print(f"\n{'— Cleaning Summary —':^60}")
    try:
        summary = vac.get_cleaning_summary(d)
        for k, v in summary.items():
            print(f"  {k:24s}: {v}")
    except Exception as e:
        print(f"  Could not read summary: {e}")

    # ── Volume ──
    vol = safe_read("volume", vac.get_volume, d)
    print(f"\n{'— Audio —':^60}")
    print(f"  {'volume':24s}: {vol}/10")

    # ── Maps ──
    print(f"\n{'— Maps —':^60}")
    map_count = safe_read("map_count", vac._get_prop, d, "map_count")
    cur_map = safe_read("current_map_id", vac._get_prop, d, "current_map_id")
    map_remember = safe_read("map_remember", vac._get_prop, d, "map_remember")
    print(f"  {'map_count':24s}: {map_count}")
    print(f"  {'current_map_id':24s}: {cur_map}")
    print(f"  {'map_remember':24s}: {'ON' if map_remember else 'OFF'}")

    try:
        map_list = vac.get_map_list(d)
        print(f"  {'map_list (raw)':24s}: {map_list}")
    except Exception as e:
        print(f"  {'map_list':24s}: Could not fetch — {e}")

    # ── Rooms ──
    print(f"\n{'— Rooms —':^60}")
    try:
        rooms = vac.get_room_list(d)
        print(f"  {'room_list (raw)':24s}: {rooms}")
    except Exception as e:
        print(f"  {'rooms':24s}: Could not fetch — {e}")

    # ── All raw properties ──
    print(f"\n{'— All Raw Properties —':^60}")
    for prop_name, (siid, piid) in vac.PROP.items():
        val = safe_read(prop_name, vac._get_prop, d, prop_name)
        print(f"  {prop_name:24s} (siid={siid:2d}, piid={piid:2d}): {val}")

    print("\n" + "=" * 60)
    print("DUMP COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
