import unittest
from types import SimpleNamespace
from unittest.mock import patch

import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
from xiaomi_details import DetailError, XiaomiDetails


class FakeMiot:
    def __init__(self, model):
        self.model = model
        self.firmware = "1.0.0"
        self.properties = {}
        self.writes = []
        self.rejected = False
        self.discard_write = False
        self.profiles = "1_4_0_1_2_0_0_1,1_5_1_0_1_1_2_0"

    def info(self, *, skip_cache):
        assert skip_cache
        return SimpleNamespace(model=self.model, firmware_version=self.firmware, mac_address="example")

    def get_properties(self, params, **_kwargs):
        return [{"did": param["did"], "code": 0, "value": self.properties.get((param["siid"], param["piid"]))}
                for param in params]

    def set_property_by(self, siid, piid, value):
        self.writes.append((siid, piid, value))
        if not self.rejected and not self.discard_write:
            self.properties[siid, piid] = value
        return [{"code": -1 if self.rejected else 0}]

    def call_action_by(self, siid, aiid, params):
        self.writes.append((siid, aiid, params))
        if (siid, aiid) == (7, 5):
            return {"code": 0, "out": [{"piid": 34, "value": self.profiles}]}
        if (siid, aiid) == (7, 4):
            self.profiles = params[0]["value"]
            return {"code": 0}
        if (siid, aiid) == (12, 1):
            self.properties.update({(12, item["piid"]): item["value"] for item in params})
            return {"code": 0}
        raise AssertionError((siid, aiid))


class XiaomiDetailsTests(unittest.TestCase):
    def setUp(self):
        self.details = XiaomiDetails()
        self.device = FakeMiot(vacuum.VACUUM_MODEL)
        self.device.properties[vacuum.PROP["mop_route"]] = 0
        vacuum_get = patch.object(vacuum, "get_device", return_value=self.device)
        vacuum_get.start()
        self.addCleanup(vacuum_get.stop)

    def test_partial_read_and_per_firmware_write_confirmation(self):
        data = self.details.vacuum()
        self.assertEqual(data["settings"]["mop_route"], {"value": 0, "writable": False})
        self.assertEqual(data["settings"]["repeat_clean"], {"value": None, "writable": False})
        self.assertFalse(data["dnd_can_verify"])
        self.assertIsNone(data["profiles"])
        with self.assertRaisesRegex(DetailError, "support"):
            self.details.change({"setting": "mop_route", "value": 1})
        self.assertEqual(self.device.writes, [])
        self.details.change({"setting": "mop_route", "verify": True})
        self.details.change({"setting": "mop_route", "value": 1})
        self.assertEqual(self.details.vacuum()["settings"]["mop_route"], {"value": 1, "writable": True})
        self.device.firmware = "1.1.0"
        self.assertFalse(self.details.vacuum()["settings"]["mop_route"]["writable"])

    def test_rejection_and_failed_readback_disable_editing(self):
        self.device.rejected = True
        with self.assertRaisesRegex(DetailError, "rejected"):
            self.details.change({"setting": "mop_route", "verify": True})
        self.device.rejected = False
        self.details.change({"setting": "mop_route", "verify": True})
        self.device.discard_write = True
        with self.assertRaisesRegex(DetailError, "confirm"):
            self.details.change({"setting": "mop_route", "value": 1})
        self.assertFalse(self.details.vacuum()["settings"]["mop_route"]["writable"])

    def test_profile_write_preserves_other_rows_and_unedited_values(self):
        self.device.properties[vacuum.PROP["current_map_id"]] = 123
        data = self.details.vacuum()
        self.assertEqual(data["map_id"], 123)
        self.assertFalse(data["profiles_writable"])
        self.details.change({"setting": "profiles", "verify": True})
        self.details.change({"setting": "profiles", "map_id": 123, "room": 4, "values": [3, 3, 1, 1]})
        self.assertEqual(self.device.profiles, "1_4_3_3_1_1_0_1,1_5_1_0_1_1_2_0")

    def test_dnd_preserves_device_timezone(self):
        self.device.properties.update({(12, 1): 0, (12, 2): 22, (12, 3): 1, (12, 4): 8, (12, 5): 3, (12, 6): 330})
        self.details.change({"setting": "dnd", "verify": True})
        self.details.change({"setting": "dnd", "dnd_enabled": 1, "dnd_start_hour": 21, "dnd_start_minute": 0,
                             "dnd_end_hour": 8, "dnd_end_minute": 30})
        self.assertEqual(self.device.properties[(12, 6)], 330)
        self.assertEqual(self.device.properties[(12, 5)], 30)
        self.assertTrue(self.details.vacuum()["dnd_writable"])

    def test_purifier_reads_zero_and_uses_the_reported_models_units(self):
        self.device.model = "zhimi.airp.rmb1"
        self.device.properties = {(4, 1): 0, (4, 3): 14, (9, 1): 391, (8, 1): 211}
        with patch.object(purifier, "get_device", return_value=self.device):
            readings = self.details.purifier()["readings"]
            self.assertEqual(readings["filter_life"]["value"], 0)
            self.assertIsNotNone(readings["filter_life"]["read_at"])
            self.assertEqual(readings["filter_used_time"]["unit"], "d")
            self.assertEqual(readings["motor_rpm"]["value"], 391)
            self.assertIsNone(readings["filter_left_days"]["read_at"])
            self.device.model = "zhimi.airp.rmb3"
            self.assertEqual(self.details.purifier()["readings"]["motor_rpm"]["value"], 211)
            self.device.model = purifier.PURIFIER_MODEL
            self.assertEqual(self.details.purifier()["readings"]["filter_used_time"]["unit"], "h")
