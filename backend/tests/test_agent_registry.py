"""The tool registry: declarations become Ollama schemas, calls are validated."""

import json
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from app.agent.registry import (
    Tool,
    ToolArgs,
    ToolContext,
    ToolError,
    ToolRegistry,
    parameters_schema,
)
from app.agent.tools import build_registry
from app.core.config import Settings


class EchoArgs(ToolArgs):
    text: str = Field(description="What to echo")
    times: int = Field(1, ge=1, le=5)
    loud: bool = False
    mode: Literal["plain", "fancy"] | None = Field(None, description="Style")


def _echo(args: EchoArgs, ctx: ToolContext) -> dict:
    if args.text == "refuse":
        raise ToolError("I won't echo that")
    if args.text == "crash":
        raise RuntimeError("a bug")
    text = args.text.upper() if args.loud else args.text
    return {"echo": [text] * args.times, "mode": args.mode}


ECHO = Tool(name="echo", description="Echoes text.", args=EchoArgs, handler=_echo)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([ECHO])


@pytest.fixture
def ctx(tmp_path) -> ToolContext:
    return ToolContext(Settings(data_dir=tmp_path))


# ------------------------------------------------------------------ schemas


def test_schema_has_the_ollama_shape(registry):
    [declaration] = registry.ollama_tools()
    assert declaration == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echoes text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to echo"},
                    "times": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
                    "loud": {"type": "boolean", "default": False},
                    "mode": {"type": "string", "enum": ["plain", "fancy"], "description": "Style"},
                },
                "required": ["text"],
            },
        },
    }


def test_optional_fields_are_plain_types_without_titles():
    schema = json.dumps(parameters_schema(EchoArgs))
    assert "anyOf" not in schema
    assert "title" not in schema
    assert '"null"' not in schema


def test_nested_arguments_are_rejected():
    class Inner(BaseModel):
        x: int

    class Nested(ToolArgs):
        inner: Inner

    with pytest.raises(ValueError, match="flat"):
        parameters_schema(Nested)


def test_every_registered_tool_has_a_valid_declaration():
    registry = build_registry()
    assert registry.names == [
        "check_inventory",
        "sales_report",
        "supplier_purchases",
        "sql_select",
        "fx_rate",
        "make_chart",
    ]
    for declaration in registry.ollama_tools():
        json.dumps(declaration)  # serializable as-is
        function = declaration["function"]
        assert function["description"].strip()
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert set(parameters.get("required", [])) <= set(parameters["properties"])
        for prop in parameters["properties"].values():
            assert "type" in prop
            assert "description" in prop or "default" in prop


@pytest.mark.parametrize("name", ["Echo", "1echo", "echo-it", "", "x" * 65])
def test_tool_names_are_snake_case(name):
    with pytest.raises(ValueError, match="Invalid tool name"):
        Tool(name=name, description="d", args=EchoArgs, handler=_echo)


def test_a_tool_needs_a_description():
    with pytest.raises(ValueError, match="description"):
        Tool(name="echo", description="  ", args=EchoArgs, handler=_echo)


def test_names_are_unique(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ECHO)


# -------------------------------------------------------------------- calls


def test_a_valid_call(registry, ctx):
    result = registry.execute("echo", {"text": "hola", "times": 2}, ctx)
    assert result.ok
    assert result.content == {"echo": ["hola", "hola"], "mode": None}
    assert result.arguments == {"text": "hola", "times": 2}
    assert json.loads(result.to_message()) == result.content


def test_arguments_as_a_json_string(registry, ctx):
    result = registry.execute("echo", '{"text": "hola", "loud": true}', ctx)
    assert result.ok
    assert result.content["echo"] == ["HOLA"]


def test_lenient_values_from_small_models(registry, ctx):
    result = registry.execute(
        "echo", {"text": "  hola ", "times": "3", "loud": "true", "mode": "", "extra": 1}, ctx
    )
    assert result.ok
    assert result.content == {"echo": ["HOLA"] * 3, "mode": None}


@pytest.mark.parametrize("arguments", [None, "", "  ", {}])
def test_missing_arguments_are_an_empty_object(registry, ctx, arguments):
    result = registry.execute("echo", arguments, ctx)
    assert not result.ok
    assert result.error == "Invalid arguments. text: Field required"


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"text"', 42])
def test_arguments_that_are_not_an_object(registry, ctx, arguments):
    result = registry.execute("echo", arguments, ctx)
    assert not result.ok
    assert result.error == "Arguments must be a JSON object"


def test_invalid_values_name_the_field(registry, ctx):
    result = registry.execute("echo", {"text": "x", "times": 9, "mode": "loud"}, ctx)
    assert not result.ok
    assert result.error.startswith("Invalid arguments. ")
    assert "times: Input should be less than or equal to 5" in result.error
    assert "mode: Input should be 'plain' or 'fancy'" in result.error
    assert result.arguments == {"text": "x", "times": 9, "mode": "loud"}


def test_unknown_tool_lists_the_known_ones(registry, ctx):
    result = registry.execute("delete_everything", {}, ctx)
    assert not result.ok
    assert result.error == "Unknown tool 'delete_everything'. Tools: echo"


def test_a_tool_error_goes_back_to_the_model(registry, ctx):
    result = registry.execute("echo", {"text": "refuse"}, ctx)
    assert not result.ok
    assert result.error == "I won't echo that"
    assert json.loads(result.to_message()) == {"error": "I won't echo that"}


def test_a_bug_in_a_handler_propagates(registry, ctx):
    with pytest.raises(RuntimeError, match="a bug"):
        registry.execute("echo", {"text": "crash"}, ctx)


def test_message_keeps_accents_and_dates(registry, ctx):
    result = registry.execute("echo", {"text": "Fundición"}, ctx)
    assert "Fundición" in result.to_message()
