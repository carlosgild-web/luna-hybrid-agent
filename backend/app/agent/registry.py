"""Tool declarations for the agent: name, description, argument model and handler.

A tool's arguments are a pydantic model. The registry turns that model into the
JSON schema Ollama's `tools` API expects, and validates what the model sends
back before a handler sees it. Handlers are plain functions; they know nothing
about the LLM, the loop or the trace (Phase 3 adds those).

`execute` never raises for a mistake the model can fix (unknown tool, arguments
that don't parse or validate, a `ToolError` from the handler): it returns a
`ToolResult` with `ok=False` and an error the model can read and retry on. Any
other exception is a bug and propagates.
"""

import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ToolError(Exception):
    """A handler's answer to arguments it can't serve; the model sees the message."""


class ToolArgs(BaseModel):
    """Base for a tool's arguments. Unknown keys are ignored, values are coerced
    where it's unambiguous ("5" -> 5, "true" -> True), which small models need."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


@dataclass(frozen=True)
class ToolContext:
    """What a handler may use. Business tools read `settings.business_db_file`."""

    settings: Settings


Handler = Callable[[Any, ToolContext], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[ToolArgs]
    handler: Handler

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"Invalid tool name {self.name!r}: use lowercase snake_case")
        if not self.description.strip():
            raise ValueError(f"Tool {self.name!r} needs a description")

    def schema(self) -> dict[str, Any]:
        """The declaration in the shape of Ollama's `tools` list."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters_schema(self.args),
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """What a call returned. `content` is shown to the model (and stored verbatim
    in the trace, invariant 11) as `to_message()`."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    content: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_message(self) -> str:
        payload = self.content if self.ok else {"error": self.error}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(
        self, name: str, arguments: Mapping[str, Any] | str | None, ctx: ToolContext
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(self._tools) or "none"
            return ToolResult(name, {}, ok=False, error=f"Unknown tool {name!r}. Tools: {known}")

        raw, problem = _parse_arguments(arguments)
        if problem:
            return ToolResult(name, {}, ok=False, error=problem)

        try:
            args = tool.args.model_validate(_drop_empty(raw))
        except ValidationError as exc:
            return ToolResult(name, raw, ok=False, error=_validation_message(exc))

        try:
            content = tool.handler(args, ctx)
        except ToolError as exc:
            return ToolResult(name, raw, ok=False, error=str(exc))
        return ToolResult(name, raw, ok=True, content=content)


# ------------------------------------------------------------------ schemas


def parameters_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of `model`, trimmed to what a small model reads well.

    Pydantic writes `Optional[X]` as `anyOf: [X, null]` with `default: null`, and adds
    a `title` everywhere. Here an optional field is just `X` left out of `required`,
    and the titles go. Nested models (`$defs`) aren't supported: keep arguments flat.
    """
    schema = model.model_json_schema()
    if "$defs" in schema:
        raise ValueError(f"{model.__name__}: tool arguments must be flat (no nested models)")
    properties = {key: _simplify(prop) for key, prop in schema.get("properties", {}).items()}
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def _simplify(prop: dict[str, Any]) -> dict[str, Any]:
    prop = {key: value for key, value in prop.items() if key != "title"}
    options = prop.pop("anyOf", None)
    if options is not None:
        concrete = [option for option in options if option.get("type") != "null"]
        if len(concrete) != 1:
            raise ValueError(f"Unsupported union in tool arguments: {options}")
        prop = {**_simplify(concrete[0]), **prop}
    if prop.get("default", ...) is None:
        del prop["default"]
    return prop


# ---------------------------------------------------------------- arguments


def _parse_arguments(arguments: Mapping[str, Any] | str | None) -> tuple[dict[str, Any], str]:
    """Ollama sends a dict; some models send a JSON string, or nothing."""
    if arguments is None:
        return {}, ""
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}, ""
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {}, "Arguments must be a JSON object"
    if not isinstance(arguments, Mapping):
        return {}, "Arguments must be a JSON object"
    return dict(arguments), ""


def _drop_empty(raw: dict[str, Any]) -> dict[str, Any]:
    """Small models fill optional arguments with null or "" instead of leaving them out."""
    return {
        key: value
        for key, value in raw.items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }


def _validation_message(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "arguments"
        problems.append(f"{where}: {error['msg']}")
    return "Invalid arguments. " + "; ".join(problems)
