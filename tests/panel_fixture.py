"""Generate a real server wire payload for the firmware host regression."""
import json

from tuch_controller_utils import flatten_status

if __name__ == "__main__":
    print(json.dumps(flatten_status({
        "lights": {"available": True, "bulbs": [
            {"name": "I1", "current_power": "on", "current_brightness": 63}]},
        "vacuum": {"available": True, "battery": 77}, "purifier": {"available": True, "pm25": 18},
    }, hour=21)))
