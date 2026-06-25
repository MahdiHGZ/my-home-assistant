"""Network-free, model-free tests for brain.py tool parsing + agentic loop.

A fake model (with a scripted create_chat_completion) drives run_prompt_stream
so the tool protocol and streaming event flow are tested without llama_cpp or
the GGUF file.
"""

import unittest

import brain


class FakeModel:
    """Minimal stand-in: returns each scripted turn as a streamed completion."""

    def __init__(self, scripted_turns):
        self.scripted = list(scripted_turns)
        self.turn = 0

    def create_chat_completion(self, messages, stream=False, **kwargs):
        text = self.scripted[self.turn] if self.turn < len(self.scripted) else ""
        self.turn += 1
        mid = max(1, len(text) // 2)
        pieces = [text[:mid], text[mid:]]

        def gen():
            for piece in pieces:
                if piece:
                    yield {"choices": [{"delta": {"content": piece}}]}
        return gen()


class ParseToolCallTests(unittest.TestCase):
    def test_fenced_tool_call_block(self):
        text = '```tool_call\n{"name": "vacuum_start", "arguments": {"room": "1"}}\n```'
        calls, cleaned = brain._parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "vacuum_start")
        self.assertEqual(calls[0]["arguments"], {"room": "1"})
        self.assertEqual(cleaned, "")

    def test_fenced_json_alias(self):
        text = 'sure\n```json\n{"name": "lights_off", "arguments": {}}\n```'
        calls, _ = brain._parse_tool_calls(text)
        self.assertEqual(calls[0]["name"], "lights_off")
        self.assertEqual(calls[0]["arguments"], {})

    def test_functiongemma_call_fallback(self):
        text = 'call:purifier_power{"on": true}'
        calls, _ = brain._parse_tool_calls(text)
        self.assertEqual(calls[0]["name"], "purifier_power")
        self.assertEqual(calls[0]["arguments"], {"on": True})

    def test_bare_json_object(self):
        calls, _ = brain._parse_tool_calls('{"name": "find_me", "arguments": {}}')
        self.assertEqual(calls[0]["name"], "find_me")

    def test_plain_text_is_not_a_tool_call(self):
        calls, cleaned = brain._parse_tool_calls("The lights are on now.")
        self.assertEqual(calls, [])
        self.assertEqual(cleaned, "The lights are on now.")

    def test_bad_json_flags_parse_error(self):
        calls, _ = brain._parse_tool_calls("call:do_thing{not json}")
        self.assertEqual(calls[0]["name"], "do_thing")
        self.assertIn("__parse_error__", calls[0]["arguments"])

    def test_looks_like_tool_call(self):
        self.assertTrue(brain._looks_like_tool_call("```tool_call"))
        self.assertTrue(brain._looks_like_tool_call('  {"name":'))
        self.assertTrue(brain._looks_like_tool_call("call:x{}"))
        self.assertFalse(brain._looks_like_tool_call("Hello, the vacuum is docked."))


class InvokeAndDocsTests(unittest.TestCase):
    def setUp(self):
        brain.clear_tools()

    def tearDown(self):
        brain.clear_tools()

    def test_invoke_known_tool(self):
        brain.register_tool(
            name="add", description="add", handler=lambda a, b: a + b,
            parameters={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
        )
        out = brain._invoke_tool("add", {"a": 2, "b": 3})
        self.assertEqual(out, {"ok": True, "result": 5})

    def test_invoke_unknown_tool(self):
        out = brain._invoke_tool("nope", {})
        self.assertFalse(out["ok"])

    def test_invoke_handler_error_captured(self):
        brain.register_tool(name="boom", description="x", parameters={"type": "object", "properties": {}},
                            handler=lambda: (_ for _ in ()).throw(RuntimeError("kaboom")))
        out = brain._invoke_tool("boom", {})
        self.assertFalse(out["ok"])
        self.assertIn("kaboom", out["error"])

    def test_tool_docs_compact_render(self):
        brain.register_tool(name="lights_set", description="Set lights.\nIgnored second line.",
                            parameters={"type": "object", "properties": {"color": {"type": "string"}}, "required": []},
                            handler=lambda color=None: None)
        docs = brain._tool_docs()
        self.assertIn("lights_set(color:string?)", docs)
        self.assertIn("Set lights.", docs)
        self.assertNotIn("second line", docs)


class AgenticLoopTests(unittest.TestCase):
    def setUp(self):
        brain.clear_tools()
        self.called = []
        brain.register_tool(
            name="ping", description="ping a device",
            parameters={"type": "object", "properties": {}},
            handler=lambda: self.called.append("ping") or {"ok": True, "pong": 1},
        )

    def tearDown(self):
        brain.clear_tools()

    def test_tool_round_then_answer(self):
        fake = FakeModel([
            '```tool_call\n{"name": "ping", "arguments": {}}\n```',
            "Done, the device responded.",
        ])
        events = list(brain.run_prompt_stream("ping it", model=fake, system_prompt="x"))
        kinds = [e["type"] for e in events]
        self.assertIn("status", kinds)
        self.assertEqual(self.called, ["ping"])
        status = next(e for e in events if e["type"] == "status")
        self.assertIn("ping", status["text"])
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertEqual(done["text"], "Done, the device responded.")

    def test_plain_answer_no_tools(self):
        fake = FakeModel(["The vacuum is already docked."])
        events = list(brain.run_prompt_stream("status?", model=fake, system_prompt="x", use_tools=False))
        self.assertEqual(self.called, [])
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertEqual(done["text"], "The vacuum is already docked.")

    def test_run_prompt_returns_final_text(self):
        fake = FakeModel(["Hi, I am Khatoon."])
        out = brain.run_prompt("hello", model=fake, system_prompt="x", use_tools=False)
        self.assertEqual(out, "Hi, I am Khatoon.")

    def test_tool_round_cap(self):
        # Model keeps calling tools forever; the round cap must stop it.
        fake = FakeModel(['```tool_call\n{"name": "ping", "arguments": {}}\n```'] * 10)
        events = list(brain.run_prompt_stream("loop", model=fake, system_prompt="x", max_tool_rounds=2))
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertIn("too many tool steps", done["text"].lower())


if __name__ == "__main__":
    unittest.main()
