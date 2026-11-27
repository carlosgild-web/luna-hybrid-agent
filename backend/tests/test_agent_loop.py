"""The agent loop against scripted fake model turns (PLAN_HYBRID.md Phase 3).

No Ollama: `FakeLLMClient.turns` scripts each reply and `chat_calls` records what the
loop sent. One test runs the real business tools on a seeded database.
"""

import datetime as dt
import json
import threading

import httpx
import pytest
from pydantic import Field

from app import cli
from app.agent.business_seed import seed_business_db
from app.agent.loop import (
    EMPTY_REPLY,
    FINAL_CALL,
    NO_ANSWER,
    AgentLimits,
    parse_calls_from_content,
    run_agent,
)
from app.agent.registry import Tool, ToolArgs, ToolContext, ToolError, ToolRegistry
from app.agent.tools import build_registry
from app.agent.trace import LLMStep, ToolStep
from app.core.config import Settings
from app.db.models import AgentTraceRecord, AuditLog
from app.llm.base import ChatTurn, ToolCall
from app.llm.fake_client import FakeLLMClient
from app.llm.ollama_client import OllamaLLMClient
from app.versioning.agent_traces import get_trace, save_trace
from app.versioning.service import NotFoundError

RESTOCK_QUESTION = "¿Qué productos necesitan resurtido?"
RESTOCK_SKUS = {"GB-1014", "CH-2004", "BN-9001"}


# ------------------------------------------------------------------ helpers


class EchoArgs(ToolArgs):
    text: str
    times: int = Field(1, ge=1, le=5)


class Counter:
    def __init__(self) -> None:
        self.calls = 0


def make_registry(counter: Counter | None = None, release: threading.Event | None = None):
    counter = counter or Counter()

    def echo(args: EchoArgs, ctx: ToolContext) -> dict:
        counter.calls += 1
        if args.text == "refuse":
            raise ToolError("I won't echo that")
        if args.text == "crash":
            raise RuntimeError("a bug in the handler")
        return {"echo": [args.text] * args.times}

    def slow(args: EchoArgs, ctx: ToolContext) -> dict:
        assert release is not None
        release.wait(5)
        return {"late": True}

    return ToolRegistry(
        [
            Tool(name="echo", description="Echoes text.", args=EchoArgs, handler=echo),
            Tool(name="slow", description="Takes its time.", args=EchoArgs, handler=slow),
        ]
    )


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(name, arguments)


def turn(*calls: ToolCall, content: str = "", tokens: tuple[int, int] = (100, 10)) -> ChatTurn:
    return ChatTurn(
        content=content,
        tool_calls=list(calls),
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
    )


def answer(text: str = "Done.") -> ChatTurn:
    return turn(content=text)


@pytest.fixture
def ctx(tmp_path) -> ToolContext:
    return ToolContext(Settings(_env_file=None, data_dir=tmp_path))


def run(llm, ctx, registry=None, **limits):
    return run_agent(
        "Echo something",
        llm=llm,
        registry=registry or make_registry(),
        ctx=ctx,
        limits=AgentLimits(**limits),
        model="fake",
        today=dt.date(2026, 11, 16),
    )


def kinds(trace) -> list[str]:
    return [step.kind for step in trace.steps]


def tool_steps(trace) -> list[ToolStep]:
    return [step for step in trace.steps if isinstance(step, ToolStep)]


# ---------------------------------------------------------- the happy path


@pytest.fixture(scope="module")
def business_settings(tmp_path_factory) -> Settings:
    path = tmp_path_factory.mktemp("business") / "hilda_luna.db"
    seed_business_db(path)
    return Settings(_env_file=None, business_db_path=path)


def test_one_tool_then_the_answer(business_settings):
    llm = FakeLLMClient(
        turns=[
            turn(call("check_inventory", only_below_reorder=True), tokens=(900, 30)),
            answer("Necesitan resurtido GB-1014, CH-2004 y BN-9001."),
        ]
    )
    trace = run_agent(
        RESTOCK_QUESTION,
        llm=llm,
        registry=build_registry(),
        ctx=ToolContext(business_settings),
        model="qwen2.5:7b",
        today=dt.date(2026, 11, 16),
    )

    assert trace.status == "answered"
    assert trace.answer == "Necesitan resurtido GB-1014, CH-2004 y BN-9001."
    assert kinds(trace) == ["llm", "tool", "llm"]
    [step] = tool_steps(trace)
    assert step.ok and step.name == "check_inventory"
    assert {item["sku"] for item in json.loads(step.result)["items"]} == RESTOCK_SKUS
    assert trace.iterations == 2
    assert (trace.prompt_tokens, trace.completion_tokens) == (900 + 100, 30 + 10)
    assert trace.tools_used == ["check_inventory"]

    first, second = llm.chat_calls
    assert {t["function"]["name"] for t in first["tools"]} == set(build_registry().names)
    system, user = first["messages"]
    assert system["role"] == "system" and "2026-11-16" in system["content"]
    assert "language of the question" in system["content"]
    assert user == {"role": "user", "content": RESTOCK_QUESTION}
    # The model is shown the tool result exactly as the trace stores it (invariant 11).
    assistant, tool_message = second["messages"][2:]
    assert assistant["tool_calls"] == [
        {"function": {"name": "check_inventory", "arguments": {"only_below_reorder": True}}}
    ]
    assert tool_message == {"role": "tool", "tool_name": "check_inventory", "content": step.result}


def test_several_calls_in_one_turn(ctx):
    llm = FakeLLMClient(turns=[turn(call("echo", text="a"), call("echo", text="b")), answer()])
    trace = run(llm, ctx)
    assert kinds(trace) == ["llm", "tool", "tool", "llm"]
    tool_messages = [m for m in llm.chat_calls[1]["messages"] if m["role"] == "tool"]
    assert [json.loads(m["content"]) for m in tool_messages] == [
        {"echo": ["a"]},
        {"echo": ["b"]},
    ]
    assert trace.status == "answered"


def test_on_step_sees_every_step_as_it_happens(ctx):
    seen = []
    trace = run_agent(
        "q",
        llm=FakeLLMClient(turns=[turn(call("echo", text="a")), answer()]),
        registry=make_registry(),
        ctx=ctx,
        on_step=seen.append,
    )
    assert seen == trace.steps and len(seen) == 3


def test_no_tool_needed(ctx):
    trace = run(FakeLLMClient(turns=[answer("Hola.")]), ctx)
    assert (trace.status, trace.answer, kinds(trace)) == ("answered", "Hola.", ["llm"])


# ----------------------------------------------------------------- mistakes


def test_unknown_tool_gets_an_error_the_model_can_read(ctx):
    llm = FakeLLMClient(turns=[turn(call("weather", city="León")), answer()])
    trace = run(llm, ctx)
    [step] = tool_steps(trace)
    assert not step.ok
    assert "Unknown tool 'weather'" in step.result and "echo" in step.result
    assert step.arguments == {"city": "León"}
    assert trace.status == "answered"


def test_bad_arguments_then_fixed(ctx):
    llm = FakeLLMClient(
        turns=[
            turn(call("echo", text="a", times=99)),
            turn(call("echo", text="a", times=2)),
            answer(),
        ]
    )
    trace = run(llm, ctx)
    bad, good = tool_steps(trace)
    assert not bad.ok and "times" in bad.result
    assert good.ok and json.loads(good.result) == {"echo": ["a", "a"]}
    assert trace.status == "answered"
    assert all(c["tools"] for c in llm.chat_calls), "tools stay offered after one failure"


def test_a_second_failure_in_a_row_stops_offering_tools(ctx):
    llm = FakeLLMClient(
        turns=[
            turn(call("echo", text="refuse")),
            turn(call("echo", text="a", times=0)),
            answer("I couldn't echo that."),
        ]
    )
    trace = run(llm, ctx)
    assert [s.ok for s in tool_steps(trace)] == [False, False]
    assert trace.status == "tool_errors"
    assert trace.answer == "I couldn't echo that."
    final = llm.chat_calls[-1]
    assert final["tools"] == []
    assert final["messages"][-1] == {"role": "user", "content": FINAL_CALL}
    assert trace.steps[-1].tools_offered is False


def test_failures_of_different_tools_dont_stop_the_run(ctx):
    llm = FakeLLMClient(
        turns=[turn(call("echo", text="refuse")), turn(call("nope")), turn(call("echo", text="ok"))]
        + [answer()]
    )
    trace = run(llm, ctx)
    assert [s.ok for s in tool_steps(trace)] == [False, False, True]
    assert trace.status == "answered"


def test_a_handler_bug_is_an_error_for_the_model_not_a_crash(ctx):
    llm = FakeLLMClient(turns=[turn(call("echo", text="crash")), answer()])
    trace = run(llm, ctx)
    [step] = tool_steps(trace)
    assert not step.ok and "internal error (RuntimeError)" in step.result
    assert "a bug in the handler" not in step.result
    assert trace.status == "answered"


def test_a_model_failure_ends_the_run_with_its_error(ctx):
    llm = FakeLLMClient(turns=[turn(call("echo", text="a")), httpx.ConnectError("refused")])
    trace = run(llm, ctx)
    assert trace.status == "llm_error"
    assert trace.error == "ConnectError: refused"
    assert trace.answer == ""
    assert kinds(trace) == ["llm", "tool"]


def test_an_empty_reply_is_retried_once_with_a_nudge(ctx):
    llm = FakeLLMClient(turns=[turn(tokens=(1118, 24)), turn(call("echo", text="a")), answer()])
    trace = run(llm, ctx)
    assert kinds(trace) == ["llm", "llm", "tool", "llm"]
    retry = llm.chat_calls[1]
    assert retry["messages"][-1] == {"role": "user", "content": EMPTY_REPLY}
    assert [m["role"] for m in retry["messages"]] == ["system", "user", "user"]
    assert retry["tools"], "the retry still offers the tools"
    assert trace.status == "answered" and trace.answer == "Done."


def test_a_second_empty_reply_ends_the_run(ctx):
    llm = FakeLLMClient(turns=[turn(), turn(call("echo", text="a")), turn()])
    trace = run(llm, ctx)
    assert (trace.status, trace.answer) == ("answered", NO_ANSWER)
    assert trace.iterations == 3


# ------------------------------------------------------------------- limits


def test_max_iterations_ends_with_a_call_without_tools(ctx):
    llm = FakeLLMClient(
        turns=[turn(call("echo", text=str(i))) for i in range(2)] + [answer("With what I have.")]
    )
    trace = run(llm, ctx, max_iterations=3)
    assert trace.iterations == 3
    assert [bool(c["tools"]) for c in llm.chat_calls] == [True, True, False]
    assert llm.chat_calls[-1]["messages"][-1]["content"] == FINAL_CALL
    assert (trace.status, trace.answer) == ("max_iterations", "With what I have.")


def test_one_iteration_means_no_tools_at_all(ctx):
    llm = FakeLLMClient(turns=[answer("Just text.")])
    trace = run(llm, ctx, max_iterations=1)
    assert llm.chat_calls[0]["tools"] == []
    assert trace.status == "max_iterations"


def test_a_final_turn_that_still_wants_tools_has_no_answer(ctx):
    llm = FakeLLMClient(turns=[turn(call("echo", text="a")), turn(call("echo", text="b"))])
    trace = run(llm, ctx, max_iterations=2)
    assert trace.answer == NO_ANSWER
    assert len(tool_steps(trace)) == 1, "a final turn's tool calls are not run"


def test_token_budget(ctx):
    llm = FakeLLMClient(
        turns=[
            turn(call("echo", text="a"), tokens=(15_000, 100)),
            turn(call("echo", text="b"), tokens=(16_000, 100)),
            answer("Short on budget."),
        ]
    )
    trace = run(llm, ctx, token_budget=30_000)
    assert [bool(c["tools"]) for c in llm.chat_calls] == [True, True, False]
    assert len(tool_steps(trace)) == 2, "the calls of the turn that passed the budget still run"
    assert (trace.status, trace.answer) == ("token_budget", "Short on budget.")


def test_a_slow_tool_times_out(ctx):
    release = threading.Event()
    try:
        llm = FakeLLMClient(turns=[turn(call("slow", text="a")), answer("It was too slow.")])
        trace = run(llm, ctx, make_registry(release=release), tool_timeout_s=0.05)
    finally:
        release.set()
    [step] = tool_steps(trace)
    assert not step.ok and "within 0.05 seconds" in step.result
    assert step.ms < 2000
    assert trace.status == "answered"


def test_the_same_call_twice_reuses_the_first_result(ctx):
    counter = Counter()
    llm = FakeLLMClient(
        turns=[
            turn(call("echo", text="a", times=2)),
            turn(ToolCall("echo", {"times": 2, "text": "a"})),  # same arguments, other order
            answer(),
        ]
    )
    trace = run(llm, ctx, make_registry(counter))
    first, second = tool_steps(trace)
    assert counter.calls == 1
    assert (first.reused, second.reused) == (False, True)
    assert second.result == first.result and second.ms == 0


def test_a_repeated_failing_call_counts_as_a_second_failure(ctx):
    llm = FakeLLMClient(
        turns=[turn(call("echo", text="refuse")), turn(call("echo", text="refuse")), answer()]
    )
    trace = run(llm, ctx)
    assert tool_steps(trace)[1].reused
    assert trace.status == "tool_errors"


# ----------------------------------------------- tool calls written as text


def test_a_tool_call_written_as_json_in_the_content_is_run(ctx):
    written = '{"name": "echo", "arguments": {"text": "hi"}}'
    llm = FakeLLMClient(turns=[turn(content=written), answer()])
    trace = run(llm, ctx)
    first = trace.steps[0]
    assert isinstance(first, LLMStep)
    assert first.parsed_from_content and first.content == written
    assert first.tool_calls == [{"name": "echo", "arguments": {"text": "hi"}}]
    [step] = tool_steps(trace)
    assert step.ok
    assistant = llm.chat_calls[1]["messages"][2]
    assert assistant["content"] == "" and assistant["tool_calls"]
    assert trace.status == "answered"


@pytest.mark.parametrize(
    "content",
    [
        '{"name": "echo", "arguments": {"text": "hi"}}',
        '{"name": "echo", "parameters": {"text": "hi"}}',
        '```json\n{"name": "echo", "arguments": {"text": "hi"}}\n```',
        '<tool_call>\n{"name": "echo", "arguments": {"text": "hi"}}\n</tool_call>',
        '[{"function": {"name": "echo", "arguments": {"text": "hi"}}}]',
        '{"name": "echo", "arguments": "{\\"text\\": \\"hi\\"}"}',
    ],
)
def test_parse_calls_from_content_shapes(content):
    [parsed] = parse_calls_from_content(content, ["echo"])
    assert parsed.name == "echo"
    assert parsed.arguments in ({"text": "hi"}, '{"text": "hi"}')


@pytest.mark.parametrize(
    "content",
    [
        "Hay 3 productos por resurtir.",
        '{"name": "weather", "arguments": {}}',
        '[{"name": "echo"}, {"name": "weather"}]',
        '{"sku": "GB-1014", "stock": 3}',
        "{not json",
        "",
    ],
)
def test_text_that_isnt_a_known_tool_call_is_an_answer(content):
    assert parse_calls_from_content(content, ["echo"]) == []


# -------------------------------------------------------------- persistence


def test_the_trace_is_stored_verbatim_with_an_audit_row(runtime, ctx):
    llm = FakeLLMClient(turns=[turn(call("echo", text="ñandú")), answer("Listo.")])
    trace = run(llm, ctx)
    with runtime.session_factory() as db:
        row = save_trace(db, trace)
        assert trace.id == row.id

    with runtime.session_factory() as db:
        stored = get_trace(db, trace.id)
        assert stored.status == "answered" and stored.answer == "Listo."
        assert stored.model == "fake" and stored.iterations == 2
        assert stored.prompt_tokens == 200 and stored.completion_tokens == 20
        assert stored.steps == trace.steps_as_dicts()
        tool_step = stored.steps[1]
        assert tool_step["kind"] == "tool"
        assert tool_step["result"] == '{"echo":["ñandú"]}'
        assert tool_step["result"] == llm.chat_calls[1]["messages"][-1]["content"]

        [audit] = db.query(AuditLog).filter_by(action="agent_run").all()
        assert audit.entity_type == "agent_trace" and audit.entity_id == trace.id
        assert "answered" in audit.detail and "tools: echo" in audit.detail

        with pytest.raises(NotFoundError):
            get_trace(db, trace.id + 1)


def test_a_failed_run_is_stored_too(runtime, ctx):
    trace = run(FakeLLMClient(turns=[httpx.ReadTimeout("slow")]), ctx)
    with runtime.session_factory() as db:
        save_trace(db, trace)
        row = db.get(AgentTraceRecord, trace.id)
        assert row.status == "llm_error" and "ReadTimeout" in row.error
        assert "tools: none" in db.query(AuditLog).filter_by(action="agent_run").one().detail


# ---------------------------------------------------------------------- CLI


@pytest.fixture
def cli_runtime(runtime, monkeypatch):
    seed_business_db(runtime.settings.business_db_file)
    monkeypatch.setattr(cli, "get_settings", lambda: runtime.settings)
    monkeypatch.setattr(cli, "build_runtime", lambda _settings: runtime)
    return runtime


def test_cli_agent_prints_the_steps_and_stores_the_trace(cli_runtime, capsys):
    cli_runtime.fake_llm.turns = [
        turn(call("check_inventory", only_below_reorder=True)),
        answer("GB-1014, CH-2004 y BN-9001."),
    ]
    assert cli.main(["agent", RESTOCK_QUESTION]) == 0
    out = capsys.readouterr().out
    assert "[1] model" in out and 'calls check_inventory {"only_below_reorder": true}' in out
    assert "[1] tool  check_inventory ok" in out and "GB-1014" in out
    assert "GB-1014, CH-2004 y BN-9001." in out
    assert "trace #1: answered, 2 model calls" in out
    with cli_runtime.session_factory() as db:
        assert get_trace(db, 1).question == RESTOCK_QUESTION


def test_cli_agent_json(cli_runtime, capsys):
    cli_runtime.fake_llm.turns = [turn(call("check_inventory", sku="CH-2004")), answer("OK.")]
    assert cli.main(["agent", "--json", "stock", "of", "CH-2004"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == 1 and data["question"] == "stock of CH-2004"
    assert [s["kind"] for s in data["steps"]] == ["llm", "tool", "llm"]
    assert json.loads(data["steps"][1]["result"])["items"][0]["sku"] == "CH-2004"


def test_cli_agent_model_failure_exits_1(cli_runtime, capsys):
    cli_runtime.fake_llm.turns = [httpx.ConnectError("Ollama is down")]
    assert cli.main(["agent", "hola"]) == 1
    assert "The model failed: ConnectError: Ollama is down" in capsys.readouterr().out


# ------------------------------------------------------------ Ollama client


def test_ollama_chat_with_tools(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "check_inventory", "arguments": {"sku": "X"}}}
                    ],
                },
                "prompt_eval_count": 812,
                "eval_count": 24,
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.llm.ollama_client.httpx.post", fake_post)
    client = OllamaLLMClient("http://ollama.test/", "qwen2.5:7b")
    tools = build_registry().ollama_tools()
    result = client.chat_with_tools([{"role": "user", "content": "q"}], tools, num_ctx=4096)

    assert sent["url"] == "http://ollama.test/api/chat"
    assert sent["body"]["tools"] == tools and sent["body"]["stream"] is False
    assert sent["body"]["options"] == {"temperature": 0.0, "num_ctx": 4096}
    assert result == ChatTurn("", [ToolCall("check_inventory", {"sku": "X"})], 812, 24)

    client.chat_with_tools([{"role": "user", "content": "q"}], [])
    assert "tools" not in sent["body"], "no tools offered: the key is left out"


def test_agent_settings():
    settings = Settings(_env_file=None, chat_model="llama3.1:8b")
    assert settings.agent_chat_model == "llama3.1:8b"
    assert Settings(_env_file=None, agent_model="qwen2.5:7b").agent_chat_model == "qwen2.5:7b"
    limits = AgentLimits.from_settings(settings)
    assert (limits.max_iterations, limits.tool_timeout_s, limits.token_budget) == (6, 20.0, 32_000)
    with pytest.raises(ValueError):
        Settings(_env_file=None, agent_max_iterations=0)


def test_the_ollama_timeout_comes_from_settings(tmp_path):
    """OLLAMA_TIMEOUT_S reaches both Ollama clients (decision 0042: video mode sets 900)."""
    from app.core.config import Settings
    from app.core.runtime import build_runtime

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", ollama_timeout_s=900)
    runtime = build_runtime(settings)
    try:
        assert runtime.llm_client_for("qwen3:4b")._timeout == 900
        assert runtime.embedding_client_for("bge-m3")._timeout == 900
        assert Settings(_env_file=None, data_dir=tmp_path / "d2").ollama_timeout_s == 300
    finally:
        runtime.engine.dispose()
