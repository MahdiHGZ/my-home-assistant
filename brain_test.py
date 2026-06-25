"""Register Xiaomi utility functions as LLM tools and run prompt tests.

Examples:
    python3 brain_test.py --list-only
    python3 brain_test.py --prompt "Check purifier PM2.5 and vacuum battery."
"""

from __future__ import annotations

import argparse
import enum
import inspect
import json
from pathlib import Path
import types
from typing import Any, get_args, get_origin

import brain
import tuch_controller_utils as panel
import xiaomi_airpurifier_utils as airpurifier
import xiaomi_vacuum_utils as vacuum

try:
    import yeelight_bulb_utils as yeelight
    _YEELIGHT_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    yeelight = None
    _YEELIGHT_IMPORT_ERROR = exc

try:
    import tapo_camera_utils as tapo
    _TAPO_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    tapo = None
    _TAPO_IMPORT_ERROR = exc


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def _humanize_name(name: str) -> str:
    return name.replace("_", " ").strip()


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    """Convert Python annotation to a JSON-schema fragment."""
    if annotation is inspect._empty or annotation is Any:
        return {"type": "string"}

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    if inspect.isclass(annotation) and issubclass(annotation, enum.IntEnum):
        enum_values = [int(v.value) for v in annotation]
        return {
            "type": "integer",
            "enum": enum_values,
            "description": f"Allowed values from {annotation.__name__}: {enum_values}",
        }

    origin = get_origin(annotation)
    if origin in (types.UnionType, getattr(types, "UnionType", object)):
        args = get_args(annotation)
    else:
        args = get_args(annotation) if origin is not None else ()

    if args:
        non_none_args = [a for a in args if a is not type(None)]  # noqa: E721
        schemas = [_schema_for_annotation(a) for a in non_none_args]
        if len(schemas) == 1:
            return schemas[0]
        return {"anyOf": schemas}

    # Fallback for unsupported annotations.
    return {"type": "string"}


def _build_tool_parameters(func: Any) -> dict[str, Any]:
    signature = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in signature.parameters.values():
        if param.name == "device":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        schema = _schema_for_annotation(param.annotation)
        if "description" not in schema:
            schema["description"] = f"{_humanize_name(param.name)}."
        properties[param.name] = schema

        if param.default is inspect._empty:
            required.append(param.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _register_module_functions(module: Any, prefix: str) -> list[str]:
    registered: list[str] = []
    for name, func in inspect.getmembers(module, inspect.isfunction):
        if func.__module__ != module.__name__:
            continue
        if name.startswith("_"):
            continue
        if not getattr(func, "__brain_tool__", False):
            continue

        tool_name = f"{prefix}_{name}"
        description = _first_line(inspect.getdoc(func))
        if not description:
            description = f"{prefix} utility: {_humanize_name(name)}."

        brain.register_tool(
            name=tool_name,
            description=description,
            parameters=_build_tool_parameters(func),
            handler=func,
        )
        registered.append(tool_name)
    return registered


def register_all_tools() -> list[str]:
    """Register every device utility marked as a brain tool. Returns tool names.

    Shared by the CLI test harness and the web interface's assistant chat so
    both expose the exact same tool set to the model.
    """
    brain.clear_tools()
    registered = _register_module_functions(airpurifier, "airpurifier")
    registered += _register_module_functions(vacuum, "vacuum")
    if yeelight is not None:
        registered += _register_module_functions(yeelight, "light")
    if tapo is not None:
        registered += _register_module_functions(tapo, "camera")
    registered += _register_module_functions(panel, "panel")
    return sorted(registered)


def main() -> None:
    parser = argparse.ArgumentParser(description="Register Xiaomi tools and test brain.run_prompt().")
    parser.add_argument(
        "--model-path",
        default=brain.DEFAULT_MODEL_PATH,
        help="Path to the GGUF model file.",
    )
    parser.add_argument(
        "--n-ctx",
        type=int,
        default=brain.DEFAULT_N_CTX,
        help="Model context window. Increase if tools/prompt exceed context.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Prompt to run after registration. If omitted, only registers/list tools.",
    )
    parser.add_argument(
        "--system-prompt-file",
        default=str(brain.DEFAULT_SYSTEM_PROMPT_PATH),
        help="Path to a file containing the system prompt.",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Inline system prompt (overrides --system-prompt-file).",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=5,
        help="Maximum tool call rounds during run_prompt.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print registered tools and exit.",
    )
    args = parser.parse_args()

    brain.clear_tools()

    registered_air = _register_module_functions(airpurifier, "airpurifier")
    registered_vac = _register_module_functions(vacuum, "vacuum")
    registered_light: list[str] = []
    if yeelight is not None:
        registered_light = _register_module_functions(yeelight, "light")
    elif _YEELIGHT_IMPORT_ERROR is not None:
        print(f"Skipping Yeelight tools: {_YEELIGHT_IMPORT_ERROR}")

    registered_camera: list[str] = []
    if tapo is not None:
        registered_camera = _register_module_functions(tapo, "camera")
    elif _TAPO_IMPORT_ERROR is not None:
        print(f"Skipping Tapo tools: {_TAPO_IMPORT_ERROR}")

    registered_panel = _register_module_functions(panel, "panel")

    all_tools = sorted(registered_air + registered_vac + registered_light
                       + registered_camera + registered_panel)

    print(f"Registered {len(all_tools)} tools.")
    print(json.dumps(all_tools, indent=2))

    if args.list_only or not args.prompt:
        return

    model_file = Path(args.model_path)
    if not model_file.exists():
        raise FileNotFoundError(
            f"Model file not found: {args.model_path}. "
            "Pass --model-path with a valid GGUF path."
        )

    brain.load_model(model_path=args.model_path, n_ctx=args.n_ctx)
    selected_system_prompt = (
        args.system_prompt
        if args.system_prompt is not None
        else brain.load_system_prompt(args.system_prompt_file)
    )
    answer = brain.run_prompt(
        args.prompt,
        system_prompt=selected_system_prompt,
        use_tools=True,
        max_tool_rounds=args.max_tool_rounds,
    )
    print("\nAssistant response:")
    print(answer)


if __name__ == "__main__":
    main()
