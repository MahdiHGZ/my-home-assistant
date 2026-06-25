"""Local LLM helpers for Khatoon — chat + agentic device control.

Targets a small CPU-only Gemma 4 GGUF (see brain_model/model_choice_design.md).
The model drives the house through the project's device tools using an
explicit, parsed tool protocol (robust across model formats), and the final
answer is streamed token-by-token so the slow CPU still feels responsive.

Public API (unchanged for callers):
    load_model(), get_model(), register_tool(), tool(), list_tools(),
    clear_tools(), run_prompt(), run_prompt_stream(),
    load_system_prompt(), get_default_system_prompt()

Tool protocol — the model calls a tool by emitting ONLY a fenced block:

    ```tool_call
    {"name": "vacuum_start_sweep", "arguments": {}}
    ```

The parser also accepts the FunctionGemma `call:name{...}` form, so a
function-calling 270M router can be dropped in later without code changes.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    from llama_cpp import Llama
    _LLAMA_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    Llama = None  # type: ignore[assignment]
    _LLAMA_IMPORT_ERROR = exc

try:
    import cv2
except ModuleNotFoundError:
    cv2 = None

# --- model / runtime defaults (tuned for the 2-core, 5.5 GB CPU server) ------
DEFAULT_MODEL_PATH = "brain_model/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
# KV cache is allocated for the whole window at load; 4k fits the system
# prompt + compact tool docs + a few tool rounds without wasting RAM.
DEFAULT_N_CTX = 4096
# Match the server's 2 physical cores; None lets llama.cpp pick (better on the
# dev Mac). Override via the COUKAB_LLM_THREADS env var.
DEFAULT_N_THREADS = int(os.getenv("COUKAB_LLM_THREADS", "0")) or None
DEFAULT_N_BATCH = 128            # smaller batch = lower peak RAM during prefill
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.4
DEFAULT_TOOL_ROUNDS = 3          # cap agentic loops; the CPU is slow

DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system.prompt"
DEFAULT_SYSTEM_PROMPT = """You are Khatoon, the smart-home assistant for this house.

Be decisive and act immediately with the tools available — never ask the user
for missing details; pick the safest sensible default and proceed. Keep spoken
replies very short: state the action taken, then one optional status line.

You control real devices (lights, vacuum, air purifier, camera, wall panel)
ONLY through tools. Never claim you did something without calling its tool.
"""

# --- tool protocol markers ---------------------------------------------------
_TOOL_INSTRUCTIONS = """

## Using tools
You can act on the house by calling tools. To call a tool, output ONLY a
fenced block (no other text) exactly like this:

```tool_call
{"name": "<tool_name>", "arguments": {<json args>}}
```

Rules:
- Output the block and nothing else when calling a tool.
- Use only the tool names listed below; use {} when a tool needs no arguments.
- You may call one tool, wait for its `TOOL RESULT`, then call another.
- When you have enough information, reply to the user in plain text (no block).

## Available tools
"""

_FENCE_RE = re.compile(r"```(?:tool_call|json|tool)?\s*(\{.*?\})\s*```", re.DOTALL)
# Args group captures from `{` to end-of-line even when unclosed, so malformed
# JSON is flagged (a recoverable parse error) instead of silently dropped.
_CALL_RE = re.compile(r"call:\s*([A-Za-z0-9_]+)\s*(\{[^\n]*)?")
_NAME_RE = re.compile(r'"name"\s*:\s*"([A-Za-z0-9_]+)"')

_llm: "Llama | None" = None
_loaded_model_path: str | None = None
_tool_handlers: dict[str, Callable[..., Any]] = {}
_tool_schemas: dict[str, dict[str, Any]] = {}


# --- system prompt -----------------------------------------------------------
def load_system_prompt(prompt_path: str | Path | None = None) -> str:
    """Read a system prompt from file, with safe fallback to the built-in."""
    target = Path(prompt_path) if prompt_path is not None else DEFAULT_SYSTEM_PROMPT_PATH
    try:
        prompt = target.read_text(encoding="utf-8").strip()
        return prompt or DEFAULT_SYSTEM_PROMPT
    except Exception:
        return DEFAULT_SYSTEM_PROMPT


def get_default_system_prompt() -> str:
    return load_system_prompt(DEFAULT_SYSTEM_PROMPT_PATH)


# --- model loading -----------------------------------------------------------
def load_model(
    model_path: str = DEFAULT_MODEL_PATH,
    force_reload: bool = False,
    **llama_kwargs: Any,
) -> "Llama":
    """Load the GGUF model once (CPU-only, small context) and return it."""
    global _llm, _loaded_model_path

    if Llama is None:
        raise RuntimeError(
            "llama-cpp-python is not installed; it is required for brain.py. "
            f"Original import error: {_LLAMA_IMPORT_ERROR}"
        )

    llama_kwargs.setdefault("n_ctx", DEFAULT_N_CTX)
    llama_kwargs.setdefault("n_gpu_layers", 0)      # CPU only
    llama_kwargs.setdefault("n_batch", DEFAULT_N_BATCH)
    llama_kwargs.setdefault("verbose", False)
    if DEFAULT_N_THREADS:
        llama_kwargs.setdefault("n_threads", DEFAULT_N_THREADS)

    if _llm is None or force_reload or _loaded_model_path != model_path:
        _llm = Llama(model_path=model_path, **llama_kwargs)
        _loaded_model_path = model_path
        # Khatoon is single-shot: every command reuses the same system+tools
        # prompt prefix. A RAM prompt cache lets llama.cpp restore that prefix
        # instead of re-prefilling it — the big win on the 2-core CPU.
        try:
            import llama_cpp
            _llm.set_cache(llama_cpp.LlamaRAMCache(capacity_bytes=512 * 1024 * 1024))
        except Exception:
            pass
    return _llm


def get_model() -> "Llama":
    return _llm if _llm is not None else load_model()


# --- tool registry -----------------------------------------------------------
def register_tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    handler: Callable[..., Any],
) -> dict[str, Any]:
    """Register a callable tool the model can invoke."""
    if not callable(handler):
        raise TypeError("handler must be callable")
    _tool_handlers[name] = handler
    _tool_schemas[name] = {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }
    return _tool_schemas[name]


def tool(*, name: str, description: str, parameters: dict[str, Any]):
    """Decorator form of :func:`register_tool`."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        register_tool(name=name, description=description, parameters=parameters, handler=func)
        return func
    return decorator


def list_tools() -> list[dict[str, Any]]:
    return list(_tool_schemas.values())


def clear_tools() -> None:
    _tool_handlers.clear()
    _tool_schemas.clear()


def _tool_docs() -> str:
    """Render registered tools compactly for the system prompt (saves prefill)."""
    lines: list[str] = []
    for schema in _tool_schemas.values():
        fn = schema["function"]
        params = (fn.get("parameters") or {}).get("properties") or {}
        required = set((fn.get("parameters") or {}).get("required") or [])
        if params:
            arg_bits = []
            for arg, spec in params.items():
                t = spec.get("type", "any")
                arg_bits.append(f"{arg}:{t}" + ("" if arg in required else "?"))
            arg_str = "(" + ", ".join(arg_bits) + ")"
        else:
            arg_str = "()"
        desc = (fn.get("description") or "").strip().splitlines()[0] if fn.get("description") else ""
        lines.append(f"- {fn['name']}{arg_str} — {desc}")
    return "\n".join(lines)


def _build_system_prompt(base: str) -> str:
    docs = _tool_docs()
    if not docs:
        return base
    return base.rstrip() + _TOOL_INSTRUCTIONS + docs


# --- tool-call parsing -------------------------------------------------------
def _parse_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extract tool calls from model output; return (calls, cleaned_text).

    Accepts fenced ```tool_call {json}``` blocks and the FunctionGemma
    `call:name{json}` form. `calls` is a list of {"name", "arguments"} dicts.
    """
    calls: list[dict[str, Any]] = []
    cleaned = text

    def _add(name: str, args_text: str | None) -> None:
        name = (name or "").strip()
        if not name:
            return
        args: dict[str, Any] = {}
        if args_text:
            try:
                parsed = json.loads(args_text)
                if isinstance(parsed, dict):
                    # `{"name":..., "arguments":{...}}` or bare arg dict.
                    if "name" in parsed and "arguments" in parsed and len(parsed) <= 3:
                        name = str(parsed["name"]) or name
                        args = parsed.get("arguments") or {}
                    else:
                        args = parsed
            except json.JSONDecodeError:
                args = {"__parse_error__": args_text}
        calls.append({"name": name, "arguments": args if isinstance(args, dict) else {}})

    # 1) fenced JSON blocks — the primary protocol.
    for m in _FENCE_RE.finditer(text):
        block = m.group(1)
        try:
            obj = json.loads(block)
            if isinstance(obj, dict) and "name" in obj:
                _add(str(obj["name"]), json.dumps(obj.get("arguments") or {}))
        except json.JSONDecodeError:
            # Salvage the tool name so the model gets a recoverable parse error.
            nm = _NAME_RE.search(block)
            if nm:
                calls.append({"name": nm.group(1), "arguments": {"__parse_error__": block}})
    if calls:
        cleaned = _FENCE_RE.sub("", text).strip()
        return calls, cleaned

    # 2) FunctionGemma `call:name{...}` fallback.
    for m in _CALL_RE.finditer(text):
        _add(m.group(1), m.group(2))
    if calls:
        cleaned = _CALL_RE.sub("", text).strip()
        return calls, cleaned

    # 3) bare leading JSON object with a "name" key.
    stripped = text.strip()
    if stripped.startswith("{") and '"name"' in stripped[:160]:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "name" in obj:
                _add(str(obj["name"]), json.dumps(obj.get("arguments") or {}))
                return calls, ""
        except json.JSONDecodeError:
            pass

    return [], text.strip()


def _looks_like_tool_call(prefix: str) -> bool:
    """Cheap early classifier on the first few chars of a streamed reply."""
    s = prefix.lstrip()
    if not s:
        return False
    return (
        s.startswith("```")
        or s.startswith("call:")
        or (s.startswith("{") and ('"name"' in s or len(s) < 12))
    )


def _invoke_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in _tool_handlers:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    if "__parse_error__" in arguments:
        return {"ok": False, "error": "Invalid JSON arguments; resend valid JSON."}
    try:
        result = _tool_handlers[name](**arguments)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "result": result}


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


# --- media helpers (kept; off by default — the chosen model is text-only) ----
_MEDIA_MAX_WIDTH = 1024
_MEDIA_JPEG_QUALITY = 75


def _image_path_to_data_uri(path_value: str) -> str:
    path = Path(path_value).expanduser().resolve()
    if cv2 is not None:
        try:
            image = cv2.imread(str(path))
            if image is not None:
                h, w = image.shape[:2]
                if w > _MEDIA_MAX_WIDTH:
                    scale = _MEDIA_MAX_WIDTH / float(w)
                    image = cv2.resize(image, (_MEDIA_MAX_WIDTH, int(h * scale)))
                ok, enc = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), _MEDIA_JPEG_QUALITY])
                if ok:
                    return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode("ascii")
        except Exception:
            pass
    payload = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


# --- the agentic chat loop ---------------------------------------------------
def run_prompt_stream(
    prompt: str,
    *,
    system_prompt: str | None = None,
    system_prompt_path: str | Path | None = None,
    use_tools: bool = True,
    max_tool_rounds: int = DEFAULT_TOOL_ROUNDS,
    model: "Llama | None" = None,
    **chat_kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """Run ONE command, yielding events as the model thinks/acts/answers.

    Single-shot by design — no conversation history. The agentic tool loop
    below appends turns only to resolve this one command; nothing persists to
    the next call. Yields dicts:
      {"type": "status", "text": "..."}  — tool activity (not the answer)
      {"type": "token",  "text": "..."}  — a piece of the final answer
      {"type": "done",   "text": "..."}  — the full final answer
    """
    llm = model if model is not None else get_model()
    base = system_prompt if system_prompt is not None else load_system_prompt(system_prompt_path)
    system = _build_system_prompt(base) if (use_tools and _tool_schemas) else base

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    chat_kwargs.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
    chat_kwargs.setdefault("temperature", DEFAULT_TEMPERATURE)

    rounds = max_tool_rounds
    while True:
        # Stream one model turn; classify tool-call vs answer from the start so
        # genuine answers stream live while tool calls stay hidden.
        buf = ""
        mode: str | None = None       # None -> undecided, "tool", "answer"
        try:
            stream = llm.create_chat_completion(messages=messages, stream=True, **chat_kwargs)
        except Exception as exc:  # noqa: BLE001
            yield {"type": "done", "text": f"(model error: {exc})"}
            return

        for chunk in stream:
            delta = (chunk["choices"][0].get("delta") or {}).get("content")
            if not delta:
                continue
            buf += delta
            if mode is None:
                stripped = buf.lstrip()
                if not use_tools or not _tool_schemas:
                    mode = "answer"
                    yield {"type": "token", "text": buf}
                elif _looks_like_tool_call(stripped):
                    mode = "tool"
                elif len(stripped) >= 12 or "\n" in stripped:
                    mode = "answer"
                    yield {"type": "token", "text": buf}
            elif mode == "answer":
                yield {"type": "token", "text": delta}

        # Decide for short replies that never crossed the threshold.
        if mode is None:
            if use_tools and _tool_schemas and _looks_like_tool_call(buf):
                mode = "tool"
            else:
                mode = "answer"
                yield {"type": "token", "text": buf}

        if mode == "answer":
            yield {"type": "done", "text": buf.strip()}
            return

        # Tool round: parse, execute, feed results back, loop.
        calls, _ = _parse_tool_calls(buf)
        if not calls:
            # Looked like a call but unparseable — surface the raw text.
            yield {"type": "done", "text": buf.strip()}
            return

        messages.append({"role": "assistant", "content": buf})
        if rounds <= 0:
            yield {"type": "done", "text": "Stopped: too many tool steps."}
            return
        rounds -= 1

        result_lines: list[str] = []
        for call in calls:
            yield {"type": "status", "text": f"using {call['name']}…"}
            result = _invoke_tool(call["name"], call.get("arguments") or {})
            result_lines.append(f"TOOL RESULT {call['name']}: {_safe_json(result)}")

        # Gemma's chat template has no `tool` role — feed results as a user turn.
        messages.append({"role": "user", "content": "\n".join(result_lines)
                         + "\n\nUse these results to answer, or call another tool."})


def run_prompt(
    prompt: str,
    *,
    system_prompt: str | None = None,
    system_prompt_path: str | Path | None = None,
    use_tools: bool = True,
    max_tool_rounds: int = DEFAULT_TOOL_ROUNDS,
    model: "Llama | None" = None,
    **chat_kwargs: Any,
) -> str:
    """Blocking single-shot wrapper: run the agentic loop, return final text."""
    final = ""
    for event in run_prompt_stream(
        prompt,
        system_prompt=system_prompt,
        system_prompt_path=system_prompt_path,
        use_tools=use_tools,
        max_tool_rounds=max_tool_rounds,
        model=model,
        **chat_kwargs,
    ):
        if event["type"] == "done":
            final = event["text"]
    return final


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    load_model()
    print(run_prompt("Hello! Introduce yourself in one sentence.", use_tools=False))
