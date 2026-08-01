"""Unit tests for pure (no-hardware) logic.

Run from the project root:
    venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import enum
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
import brain_test
import yeelight_bulb_utils as ybu


class ParseColorInputTests(unittest.TestCase):
    def test_mode_name_rgb(self):
        self.assertEqual(ybu._parse_color_input("red"), ("rgb", (255, 0, 0)))

    def test_mode_name_color_temp(self):
        self.assertEqual(ybu._parse_color_input("warm_white"), ("ct", 3000))

    def test_kelvin_alias(self):
        self.assertEqual(ybu._parse_color_input("cool white"), ("ct", 6500))

    def test_hex(self):
        self.assertEqual(ybu._parse_color_input("#FF8040"), ("rgb", (255, 128, 64)))

    def test_invalid_hex_raises(self):
        with self.assertRaises(ValueError):
            ybu._parse_color_input("#GGGGGG")

    def test_rgb_string(self):
        self.assertEqual(ybu._parse_color_input("10, 20, 30"), ("rgb", (10, 20, 30)))

    def test_rgb_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            ybu._parse_color_input("300,0,0")

    def test_kelvin_digits(self):
        self.assertEqual(ybu._parse_color_input("3000"), ("ct", 3000))

    def test_kelvin_digits_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            ybu._parse_color_input("9000")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            ybu._parse_color_input("not-a-color")


class ResolveBulbTargetsTests(unittest.TestCase):
    REGISTERED = {"I2": "ip2", "I1": "ip1", "I3": "ip3"}

    def test_none_selects_all_sorted(self):
        targets = ybu._resolve_bulb_targets(self.REGISTERED, None)
        self.assertEqual(targets, [("I1", "ip1"), ("I2", "ip2"), ("I3", "ip3")])

    def test_all_and_star_select_everything(self):
        for token in ("all", "*", ""):
            targets = ybu._resolve_bulb_targets(self.REGISTERED, token)
            self.assertEqual(len(targets), 3, token)

    def test_comma_separated_names(self):
        targets = ybu._resolve_bulb_targets(self.REGISTERED, "I3, I1")
        self.assertEqual(targets, [("I1", "ip1"), ("I3", "ip3")])

    def test_case_insensitive(self):
        targets = ybu._resolve_bulb_targets(self.REGISTERED, "i2")
        self.assertEqual(targets, [("I2", "ip2")])

    def test_duplicates_collapse(self):
        targets = ybu._resolve_bulb_targets(self.REGISTERED, ["I1", "i1", "I1"])
        self.assertEqual(targets, [("I1", "ip1")])

    def test_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            ybu._resolve_bulb_targets(self.REGISTERED, "I9")

    def test_empty_iterable_raises(self):
        with self.assertRaises(ValueError):
            ybu._resolve_bulb_targets(self.REGISTERED, ["  "])

    def test_empty_registry(self):
        self.assertEqual(ybu._resolve_bulb_targets({}, None), [])


class BulbBatchFailureTests(unittest.TestCase):
    def tearDown(self):
        ybu._state_stack.clear()

    def test_apply_mode_reports_named_partial_failure(self):
        class BrokenBulb:
            def turn_on(self):
                raise OSError("offline")

        with mock.patch.object(ybu, "_get_bulb", return_value=BrokenBulb()):
            with self.assertRaises(ybu.BulbBatchError) as raised:
                ybu.apply_mode({"Kitchen": "192.0.2.1"}, "red")

        self.assertEqual(set(raised.exception.failures), {"Kitchen"})

    def test_failed_restore_keeps_undo_entry(self):
        saved = {
            "Kitchen": {
                "ip": "192.0.2.1", "power": "on", "bright": 50,
                "color_mode": 1, "ct": 3000, "rgb": 0xFF0000,
            }
        }
        ybu._state_stack.append(saved)

        with mock.patch.object(ybu, "apply_state", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                ybu.restore_state({"Kitchen": "192.0.2.1"})

        self.assertEqual(ybu.undo_depth(), 1)
        self.assertIs(ybu._state_stack[-1], saved)


class ParseToolCallsTests(unittest.TestCase):
    """The current fenced-JSON / `call:` tool protocol (see test_brain_tools
    for the full agentic-loop coverage)."""

    def test_fenced_call_with_args(self):
        content = 'before\n```tool_call\n{"name": "do_thing", "arguments": {"a": 1}}\n```'
        calls, cleaned = brain._parse_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "do_thing")
        self.assertEqual(calls[0]["arguments"], {"a": 1})
        self.assertEqual(cleaned, "before")

    def test_call_form_without_args(self):
        calls, _ = brain._parse_tool_calls("call:ping")
        self.assertEqual(calls[0]["name"], "ping")
        self.assertEqual(calls[0]["arguments"], {})

    def test_malformed_json_returns_parse_error_for_known_tool(self):
        brain.clear_tools()
        try:
            brain.register_tool(
                name="echo", description="Echo.",
                parameters={"type": "object", "properties": {}},
                handler=lambda **kw: kw,
            )
            calls, _ = brain._parse_tool_calls("call:echo {bad json")
            result = brain._invoke_tool(calls[0]["name"], calls[0]["arguments"])
            self.assertFalse(result["ok"])
            self.assertIn("Invalid JSON", result["error"])
        finally:
            brain.clear_tools()

    def test_plain_text_is_not_a_call(self):
        calls, cleaned = brain._parse_tool_calls("All lights are off now.")
        self.assertEqual(calls, [])
        self.assertEqual(cleaned, "All lights are off now.")


class SchemaForAnnotationTests(unittest.TestCase):
    def test_primitives(self):
        self.assertEqual(brain_test._schema_for_annotation(str), {"type": "string"})
        self.assertEqual(brain_test._schema_for_annotation(int), {"type": "integer"})
        self.assertEqual(brain_test._schema_for_annotation(float), {"type": "number"})
        self.assertEqual(brain_test._schema_for_annotation(bool), {"type": "boolean"})

    def test_int_enum(self):
        class Level(enum.IntEnum):
            LOW = 1
            HIGH = 3

        schema = brain_test._schema_for_annotation(Level)
        self.assertEqual(schema["type"], "integer")
        self.assertEqual(schema["enum"], [1, 3])

    def test_optional_collapses_to_inner(self):
        schema = brain_test._schema_for_annotation(int | None)
        self.assertEqual(schema, {"type": "integer"})

    def test_union_becomes_any_of(self):
        schema = brain_test._schema_for_annotation(int | str)
        self.assertEqual(schema, {"anyOf": [{"type": "integer"}, {"type": "string"}]})

    def test_fallback_is_string(self):
        self.assertEqual(brain_test._schema_for_annotation(dict), {"type": "string"})


class BuildToolParametersTests(unittest.TestCase):
    def test_device_param_is_skipped_and_required_tracked(self):
        def sample(level: int, name: str = "x", device=None):
            pass

        params = brain_test._build_tool_parameters(sample)
        self.assertEqual(set(params["properties"]), {"level", "name"})
        self.assertEqual(params["required"], ["level"])


if __name__ == "__main__":
    unittest.main()
