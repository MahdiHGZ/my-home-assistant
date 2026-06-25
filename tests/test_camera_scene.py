"""Tests for the camera scene-analysis helpers (no camera / network needed).

Feeds synthetic frames to analyze_frame() so the lighting classification,
presence fields, and change detection are exercised without a Tapo camera.
"""

import unittest

import numpy as np

import tapo_camera_utils as tapo


class BrightnessLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(tapo._brightness_label(0), "dark")
        self.assertEqual(tapo._brightness_label(60), "dim")
        self.assertEqual(tapo._brightness_label(120), "well lit")
        self.assertEqual(tapo._brightness_label(255), "bright")


class AnalyzeFrameTests(unittest.TestCase):
    def setUp(self):
        tapo._last_scene_gray = None  # isolate change detection per test

    def test_dark_frame(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        out = tapo.analyze_frame(frame)
        self.assertTrue(out["ok"])
        self.assertEqual(out["lighting"], "dark")
        self.assertEqual(out["brightness_pct"], 0)
        self.assertEqual(out["people"], 0)
        self.assertFalse(out["someone_present"])
        self.assertIn("dark", out["summary"])

    def test_bright_frame(self):
        frame = np.full((120, 160, 3), 255, dtype=np.uint8)
        out = tapo.analyze_frame(frame)
        self.assertEqual(out["lighting"], "bright")
        self.assertEqual(out["brightness_pct"], 100)

    def test_first_look_has_no_change_then_detects_change(self):
        first = tapo.analyze_frame(np.zeros((120, 160, 3), dtype=np.uint8))
        self.assertIsNone(first["changed_since_last"])      # nothing to compare to
        second = tapo.analyze_frame(np.full((120, 160, 3), 255, dtype=np.uint8))
        self.assertTrue(second["changed_since_last"])       # dark -> bright = change

    def test_no_change_on_identical_frames(self):
        tapo.analyze_frame(np.full((120, 160, 3), 130, dtype=np.uint8))
        again = tapo.analyze_frame(np.full((120, 160, 3), 130, dtype=np.uint8))
        self.assertFalse(again["changed_since_last"])


class ToolRegistrationTests(unittest.TestCase):
    def test_look_around_is_a_brain_tool(self):
        self.assertTrue(getattr(tapo.look_around, "__brain_tool__", False))


if __name__ == "__main__":
    unittest.main()
