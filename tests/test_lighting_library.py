import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lighting_library
import main
from household import Household


class LightingLibraryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "house.sqlite"
        self.store = Household(self.path)

    def test_groups_and_current_scenes_persist_without_device_addresses(self):
        bulbs = {"A": "192.0.2.1", "B": "192.0.2.2"}
        state = {"A": {"ip": bulbs["A"], "power": "on", "color_mode": 2, "ct": 3000, "bright": 35}}
        with (
            patch.object(lighting_library.yeelight, "load_registered_bulbs", return_value=bulbs),
            patch.object(lighting_library.yeelight, "read_state", return_value=state) as read,
        ):
            lighting_library.save_entry(self.store, "group", "Downstairs", ["A", "B"])
            lighting_library.save_entry(self.store, "scene", "Reading", ["A"])
            read.assert_called_once_with({"A": bulbs["A"]})
        reopened = Household(self.path)
        view = lighting_library.list_entries(reopened, {"A": bulbs["A"]})
        self.assertEqual(view["groups"], [{"name": "Downstairs", "bulbs": ["A", "B"], "missing": ["B"]}])
        self.assertEqual(view["scenes"], [{"name": "Reading", "bulbs": ["A"], "missing": []}])
        with reopened.connection() as db:
            data = db.execute("SELECT data FROM lighting_library WHERE kind = 'scene'").fetchone()[0]
        self.assertNotIn(bulbs["A"], data)

    def test_recall_uses_only_registered_members_of_the_scene(self):
        bulbs = {"A": "192.0.2.1", "B": "192.0.2.2", "Unrelated": "192.0.2.3"}
        state = {"A": {"power": "on", "color_mode": 1, "rgb": 0xFFEEDD, "bright": 20},
                 "B": {"power": "off"}}
        with (
            patch.object(lighting_library.yeelight, "load_registered_bulbs", return_value=bulbs) as load,
            patch.object(lighting_library.yeelight, "read_state", return_value=state),
            patch.object(lighting_library.yeelight, "is_party_running", return_value=False),
            patch.object(lighting_library.yeelight, "save_state") as save,
            patch.object(lighting_library.yeelight, "apply_state") as apply,
        ):
            lighting_library.save_entry(self.store, "scene", "Both", ["A", "B"])
            load.return_value = {"A": bulbs["A"], "Unrelated": bulbs["Unrelated"]}
            result = lighting_library.recall_scene(self.store, "Both")
            self.assertEqual(result, {"ok": True, "missing": ["B"]})
            save.assert_called_once_with({"A": bulbs["A"]})
            apply.assert_called_once_with({"A": bulbs["A"]}, state)
            load.return_value = {"Unrelated": bulbs["Unrelated"]}
            with self.assertRaisesRegex(ValueError, "still registered"):
                lighting_library.recall_scene(self.store, "Both")
            self.assertEqual(apply.call_count, 1)

    def test_printable_key_map_matches_dispatch(self):
        bindings = main.keypad_bindings()
        self.assertEqual({f"KEY_{entry['code']}" for entry in bindings if entry["combo"]}, main._COMBO_KEYS)
        self.assertEqual({f"KEY_{entry['code']}" for entry in bindings if not entry["combo"]}, main._SINGLE_KEYS)
        self.assertEqual(len(bindings), len(main._COMBO_KEYS) + len(main._SINGLE_KEYS))
