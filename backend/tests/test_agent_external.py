"""`fx_rate` (PLAN_HYBRID.md Phase 5, decision 0037), with no network: live rates come
from an `httpx.MockTransport` that counts the requests it gets.

The phase's "done when", on fakes: with ALLOW_EXTERNAL_APIS off the tool answers with
the stored rate and says so, without a request; with it on, a live rate is fetched,
cached and audited, and a failed request degrades to the stored rate.
"""

import datetime as dt
import json

import httpx
import pytest

import app.agent.tools.external as external
from app.agent.business_seed import TIPO_CAMBIO, seed_business_db
from app.agent.loop import FX_RULE, run_agent, system_prompt
from app.agent.registry import ToolContext, ToolRegistry
from app.agent.tools import build_registry
from app.agent.tools.external import (
    FIXED_RATE_NOTE,
    STORED_NOTE,
    FxRates,
    external_tools,
    shared_rates,
)
from app.core.config import Settings
from app.db.models import AuditLog
from app.llm.base import ChatTurn, ToolCall
from app.versioning.agent_traces import save_trace

TODAY = dt.date(2026, 11, 16)
LIVE = 18.5698


@pytest.fixture(scope="module")
def business_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("business") / "hilda_luna.db"
    seed_business_db(path)
    return path


class Provider:
    """A fake frankfurter v2: `/rate/USD/MXN[?date=...]`, or the failure it's told."""

    def __init__(self, fail: Exception | int | str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, int):
            return httpx.Response(self.fail, json={"status": self.fail, "message": "nope"})
        if self.fail == "garbage":
            return httpx.Response(200, text="<html>maintenance</html>")
        day = request.url.params.get("date", TODAY.isoformat())
        return httpx.Response(200, json={"date": day, "base": "USD", "quote": "MXN", "rate": LIVE})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_ctx(business_db, provider: Provider, *, allow: bool, clock=None) -> ToolContext:
    settings = Settings(_env_file=None, business_db_path=business_db, allow_external_apis=allow)
    rates = FxRates(
        "https://fx.test/v2",
        timeout_s=1,
        ttl_s=60,
        transport=httpx.MockTransport(provider),
        clock=clock or Clock(),
    )
    return ToolContext(settings, fx=rates).for_new_run()


def ask(ctx: ToolContext, **arguments):
    return ToolRegistry(external_tools()).execute("fx_rate", arguments, ctx)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(external, "_today", lambda: TODAY)


# ------------------------------------------------------------ flag off


def test_with_external_apis_off_it_answers_with_the_stored_rate_and_says_so(business_db):
    provider = Provider()
    result = ask(make_ctx(business_db, provider, allow=False))

    assert result.ok
    assert result.content["rate"] == TIPO_CAMBIO == 18.5
    assert result.content["source"] == "stored"
    assert result.content["note"] == STORED_NOTE
    assert result.content["stored_rate_as_of"]
    assert result.meta == {"source": "stored"}  # nothing to audit
    assert provider.requests == []


def test_a_stored_rate_for_a_day_says_the_books_use_one_rate(business_db):
    result = ask(make_ctx(business_db, Provider(), allow=False), date="2026-07-15")
    assert result.content["rate"] == 18.5
    assert FIXED_RATE_NOTE in result.content["note"]


def test_the_default_settings_keep_external_apis_off():
    assert Settings(_env_file=None).allow_external_apis is False


def test_it_converts_both_ways_so_the_model_does_no_arithmetic(business_db):
    ctx = make_ctx(business_db, Provider(), allow=False)
    to_mxn = ask(ctx, amount=1000, currency="USD").content["conversion"]
    to_usd = ask(ctx, amount="519725.51", currency="MXN").content["conversion"]

    assert to_mxn == {
        "amount": 1000.0,
        "currency": "USD",
        "converted": 18500.0,
        "converted_currency": "MXN",
    }
    assert to_usd["converted"] == round(519725.51 / 18.5, 2) == 28093.27
    assert to_usd["converted_currency"] == "USD"


def test_a_future_day_or_a_bad_amount_is_an_error_the_model_can_fix(business_db):
    ctx = make_ctx(business_db, Provider(), allow=True)
    future = ask(ctx, date="2027-01-01")
    negative = ask(ctx, amount=-5)

    assert not future.ok and "future" in future.error
    assert not negative.ok and "amount" in negative.error


def test_without_a_business_database_the_stored_rate_is_an_error(tmp_path):
    result = ask(make_ctx(tmp_path / "missing.db", Provider(), allow=False))
    assert not result.ok
    assert "seed-business-db" in result.error


# ------------------------------------------------------------- flag on


def test_with_external_apis_on_it_fetches_the_live_rate(business_db):
    provider = Provider()
    result = ask(make_ctx(business_db, provider, allow=True), amount=100)

    assert result.ok
    assert result.content["source"] == "live"
    assert result.content["rate"] == LIVE
    assert result.content["rate_date"] == TODAY.isoformat()
    assert result.content["conversion"]["converted"] == 1856.98
    assert "note" not in result.content
    assert [str(r.url) for r in provider.requests] == ["https://fx.test/v2/rate/USD/MXN"]
    call = result.meta["external_call"]
    assert call["ok"] is True and call["rate"] == LIVE
    assert call["url"] == "https://fx.test/v2/rate/USD/MXN"


def test_a_day_is_asked_for_by_date(business_db):
    provider = Provider()
    result = ask(make_ctx(business_db, provider, allow=True), date="2026-07-15")
    assert result.content["rate_date"] == "2026-07-15"
    assert provider.requests[0].url.params["date"] == "2026-07-15"


def test_a_rate_from_an_earlier_day_is_noted(business_db):
    def weekend(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"date": "2026-11-13", "base": "USD", "quote": "MXN", "rate": 18.4}
        )

    settings = Settings(_env_file=None, business_db_path=business_db, allow_external_apis=True)
    rates = FxRates("https://fx.test/v2", transport=httpx.MockTransport(weekend))
    result = ask(ToolContext(settings, fx=rates), date="2026-11-15")
    assert "2026-11-13" in result.content["note"]


def test_the_live_rate_is_cached_and_a_cached_answer_is_not_a_new_call(business_db):
    provider, clock = Provider(), Clock()
    ctx = make_ctx(business_db, provider, allow=True, clock=clock)

    first = ask(ctx)
    second = ask(ctx, amount=10)  # other arguments, same rate
    assert len(provider.requests) == 1
    assert "external_call" in first.meta
    assert "external_call" not in second.meta and second.meta["cached"] is True
    assert second.content["rate"] == LIVE

    clock.now += 61  # past FX_CACHE_TTL_S
    third = ask(ctx)
    assert len(provider.requests) == 2 and "external_call" in third.meta


def test_a_past_days_rate_stays_cached(business_db):
    provider, clock = Provider(), Clock()
    ctx = make_ctx(business_db, provider, allow=True, clock=clock)
    ask(ctx, date="2026-07-15")
    clock.now += 10_000
    ask(ctx, date="2026-07-15")
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ConnectTimeout("slow"), "no answer within 1 s"),
        (httpx.ConnectError("offline"), "request failed: ConnectError"),
        (503, "HTTP 503"),
        ("garbage", "a reply it couldn't read"),
    ],
)
def test_a_failed_request_degrades_to_the_stored_rate(business_db, failure, reason):
    provider = Provider(fail=failure)
    result = ask(make_ctx(business_db, provider, allow=True), amount=100)

    assert result.ok  # never an error answer (invariant 10)
    assert result.content["source"] == "stored"
    assert result.content["rate"] == 18.5
    assert result.content["conversion"]["converted"] == 1850.0
    assert reason in result.content["note"]
    assert "stored in the company's books" in result.content["note"]
    assert result.meta["external_call"] == {
        "service": "frankfurter.dev",
        "url": "https://fx.test/v2/rate/USD/MXN",
        "ok": False,
        "error": reason,
    }


def test_a_failure_is_not_cached(business_db):
    provider = Provider(fail=503)
    ctx = make_ctx(business_db, provider, allow=True)
    ask(ctx)
    provider.fail = None
    assert ask(ctx).content["source"] == "live"
    assert len(provider.requests) == 2


def test_the_shared_client_outlives_a_run():
    settings = Settings(_env_file=None)
    assert shared_rates(settings) is shared_rates(settings)
    assert shared_rates(settings).base_url == "https://api.frankfurter.dev/v2"


# ----------------------------------------------- the run, the trace, the audit


def fx_turns(answer: str, **arguments) -> list[ChatTurn]:
    return [
        ChatTurn(content="", tool_calls=[ToolCall("fx_rate", arguments)]),
        ChatTurn(content=answer),
    ]


def run_fx(runtime, business_db, provider, *, allow, answer, **arguments):
    ctx = make_ctx(business_db, provider, allow=allow)
    runtime.fake_llm.turns = fx_turns(answer, **arguments)
    trace = run_agent(
        "How many pesos is 100 dollars today?",
        llm=runtime.fake_llm,
        registry=build_registry(),
        ctx=ctx,
        today=TODAY,
    )
    with runtime.session_factory() as db:
        save_trace(db, trace)
        audit = db.query(AuditLog).filter(AuditLog.entity_id == trace.id).all()
    return trace, audit


def test_flag_off_run_answers_from_the_stored_rate_and_audits_no_call(runtime, business_db):
    trace, audit = run_fx(
        runtime,
        business_db,
        Provider(),
        allow=False,
        answer="At the rate stored in the books, 18.50 MXN per USD, 100 USD are 1,850 MXN.",
        amount=100,
    )
    shown = json.loads(trace.steps[1].result)
    assert shown["source"] == "stored" and shown["note"] == STORED_NOTE
    assert trace.checks["unsupported_numbers"] == []
    assert [a.action for a in audit] == ["agent_run"]


def test_flag_on_run_audits_the_live_call(runtime, business_db):
    trace, audit = run_fx(
        runtime,
        business_db,
        Provider(),
        allow=True,
        answer="Today's rate is 18.5698 MXN per USD, so 100 USD are 1,856.98 MXN.",
        amount=100,
    )
    assert trace.checks["unsupported_numbers"] == []
    calls = [a for a in audit if a.action == "external_call"]
    assert len(calls) == 1
    assert calls[0].entity_type == "agent_trace"
    assert calls[0].detail.startswith(
        "fx_rate: GET https://fx.test/v2/rate/USD/MXN ok, rate 18.5698 ("
    )


def test_a_failed_live_call_is_audited_too(runtime, business_db):
    _, audit = run_fx(
        runtime, business_db, Provider(fail=503), allow=True, answer="18.50 MXN.", amount=1
    )
    (call,) = [a for a in audit if a.action == "external_call"]
    assert "failed (HTTP 503)" in call.detail
    assert "stored value" in call.detail


def test_a_reused_call_is_audited_once(runtime, business_db):
    ctx = make_ctx(business_db, Provider(), allow=True)
    runtime.fake_llm.turns = [
        ChatTurn(content="", tool_calls=[ToolCall("fx_rate", {})]),
        ChatTurn(content="", tool_calls=[ToolCall("fx_rate", {})]),
        ChatTurn(content="18.5698"),
    ]
    trace = run_agent(
        "Rate?", llm=runtime.fake_llm, registry=build_registry(), ctx=ctx, today=TODAY
    )
    with runtime.session_factory() as db:
        save_trace(db, trace)
        calls = db.query(AuditLog).filter(AuditLog.action == "external_call").count()
    assert [s.reused for s in trace.steps if s.kind == "tool"] == [False, True]
    assert calls == 1


def test_the_prompt_tells_the_model_to_disclose_a_stored_rate():
    prompt = system_prompt(TODAY, fx=True)
    assert FX_RULE in prompt
    assert '"stored"' in prompt
    assert FX_RULE not in system_prompt(TODAY)
