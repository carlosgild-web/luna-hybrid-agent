"""`fx_rate`: the USD/MXN exchange rate, the agent's one external tool (decision 0037).

Local-first, external by explicit consent (agent invariant 10):

- `ALLOW_EXTERNAL_APIS=false` (the default): no request leaves the machine. The tool
  answers with the rate stored in the business database (every order is booked at
  18.50 MXN per USD) and tells the model to say so.
- `ALLOW_EXTERNAL_APIS=true`: the tool asks a frankfurter.dev v2 API (`FX_API_URL`, no
  key) for the day's rate. Answers are cached in the process: today's rate for
  `FX_CACHE_TTL_S`, a past day's for good. A request that fails (timeout, HTTP error,
  a reply it can't read) degrades to the stored rate with a note, never to an error.

Every request that was actually sent is described in the step's `meta` under
`external_call`; `versioning/agent_traces.py` turns each one into an `external_call`
audit row when the trace is saved, so the handler stays free of database writers.

With `amount`, the tool also converts, so the answer's numbers come from the tool and
not from the model's arithmetic (invariant 12).
"""

import datetime as dt
import threading
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Literal

import httpx
from pydantic import Field

from app.agent.business_db import BusinessDBMissingError, open_business_db
from app.agent.registry import Tool, ToolArgs, ToolContext, ToolError, ToolOutput
from app.core.config import Settings

BASE, QUOTE = "USD", "MXN"
PROVIDER = "frankfurter.dev"
_MAX_CACHED_DAYS = 512

STORED_NOTE = (
    "External APIs are turned off (ALLOW_EXTERNAL_APIS=false), so this is the rate "
    "stored in the company's books, not today's market rate. Say so in your answer."
)
FALLBACK_NOTE = (
    "The live rate couldn't be fetched ({reason}), so this is the rate stored in the "
    "company's books, not the market rate. Say so in your answer."
)
FIXED_RATE_NOTE = "The books use this one rate for every order, whatever the day."


class FxUnavailableError(Exception):
    """The live rate couldn't be fetched; the message says why, in a few words."""


@dataclass(frozen=True)
class LiveRate:
    rate: float  # MXN per USD
    day: str  # the day the provider says the rate is for
    url: str
    cached: bool
    ms: int


class FxRates:
    """Live USD->MXN rates over HTTP, with an in-process cache. One per process (the
    cache is shared by every run); tests pass their own with an `httpx.MockTransport`."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 5.0,
        ttl_s: float = 3600.0,
        transport: httpx.BaseTransport | None = None,
        clock=time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.ttl_s = ttl_s
        self.transport = transport
        self.clock = clock
        self._cache: dict[str, tuple[float, LiveRate]] = {}  # key -> (stored at, rate)
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "FxRates":
        return cls(
            settings.fx_api_url, timeout_s=settings.fx_timeout_s, ttl_s=settings.fx_cache_ttl_s
        )

    def url_for(self, day: dt.date | None) -> str:
        url = f"{self.base_url}/rate/{BASE}/{QUOTE}"
        return f"{url}?date={day.isoformat()}" if day else url

    def get(self, day: dt.date | None, today: dt.date) -> LiveRate:
        """The rate for `day` (None: the latest). Raises `FxUnavailableError`."""
        key = day.isoformat() if day else "latest"
        expires = day is None or day >= today  # a past day's rate doesn't change
        with self._lock:
            hit = self._cache.get(key)
            if hit and not (expires and self.clock() - hit[0] > self.ttl_s):
                return replace(hit[1], cached=True, ms=0)

        rate = self._fetch(day)
        with self._lock:
            if len(self._cache) >= _MAX_CACHED_DAYS:
                self._cache.clear()
            self._cache[key] = (self.clock(), rate)
        return rate

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _fetch(self, day: dt.date | None) -> LiveRate:
        url = self.url_for(day)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.get(url, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            raise FxUnavailableError(f"no answer within {self.timeout_s:g} s") from exc
        except httpx.HTTPError as exc:
            raise FxUnavailableError(f"request failed: {type(exc).__name__}") from exc
        ms = round((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            raise FxUnavailableError(f"HTTP {response.status_code}")
        try:
            body = response.json()
            rate = float(body["rate"])
            when = str(body.get("date") or (day.isoformat() if day else ""))
        except (ValueError, KeyError, TypeError) as exc:
            raise FxUnavailableError("a reply it couldn't read") from exc
        if not rate > 0 or body.get("base", BASE) != BASE or body.get("quote", QUOTE) != QUOTE:
            raise FxUnavailableError("a reply it couldn't read")
        return LiveRate(rate=rate, day=when, url=url, cached=False, ms=ms)


@lru_cache(maxsize=4)
def _shared(base_url: str, timeout_s: float, ttl_s: float) -> FxRates:
    return FxRates(base_url, timeout_s=timeout_s, ttl_s=ttl_s)


def shared_rates(settings: Settings) -> FxRates:
    """The process-wide client for these settings, so its cache outlives one run."""
    return _shared(settings.fx_api_url, settings.fx_timeout_s, settings.fx_cache_ttl_s)


# ------------------------------------------------------------------ the tool


class FxRateArgs(ToolArgs):
    date: dt.date | None = Field(
        None, description="The day of the rate, YYYY-MM-DD. Leave out for today's rate."
    )
    amount: float | None = Field(
        None, gt=0, description="An amount to convert at this rate, e.g. 519725.51"
    )
    currency: Literal["USD", "MXN"] = Field(
        "USD", description="The currency `amount` is in; it's converted to the other one"
    )


def stored_rate(settings: Settings) -> tuple[float, str]:
    """The rate the books use, from the latest order, and that order's date."""
    try:
        cx = open_business_db(settings.business_db_file)
    except BusinessDBMissingError as exc:
        raise ToolError(str(exc)) from exc
    try:
        row = cx.execute(
            "SELECT tipo_cambio, fecha FROM pedidos ORDER BY fecha DESC, pedido_id DESC LIMIT 1"
        ).fetchone()
    finally:
        cx.close()
    if row is None:
        raise ToolError("The business database has no orders, so it has no stored rate.")
    return float(row["tipo_cambio"]), row["fecha"]


def _today() -> dt.date:
    return dt.date.today()


def fx_rate(args: FxRateArgs, ctx: ToolContext) -> ToolOutput:
    today = _today()
    if args.date and args.date > today:
        raise ToolError(f"There is no exchange rate for a future day ({args.date}).")

    meta: dict[str, Any] = {}
    live: LiveRate | None = None
    reason = ""
    if ctx.settings.allow_external_apis:
        rates = ctx.fx or shared_rates(ctx.settings)
        try:
            live = rates.get(args.date, today)
        except FxUnavailableError as exc:
            reason = str(exc)
            meta["external_call"] = {
                "service": PROVIDER,
                "url": rates.url_for(args.date),
                "ok": False,
                "error": reason,
            }
        else:
            if not live.cached:
                meta["external_call"] = {
                    "service": PROVIDER,
                    "url": live.url,
                    "ok": True,
                    "rate": live.rate,
                    "ms": live.ms,
                }
            meta["cached"] = live.cached

    content: dict[str, Any] = {"base": BASE, "quote": QUOTE}
    notes: list[str] = []
    if live is not None:
        rate = live.rate
        content.update(rate=rate, rate_date=live.day, source="live", provider=PROVIDER)
        asked = (args.date or today).isoformat()
        if live.day and live.day != asked:
            notes.append(f"The provider's latest rate on or before {asked} is from {live.day}.")
    else:
        rate, as_of = stored_rate(ctx.settings)
        content.update(rate=rate, source="stored", stored_rate_as_of=as_of)
        notes.append(FALLBACK_NOTE.format(reason=reason) if reason else STORED_NOTE)
        if args.date:
            notes.append(FIXED_RATE_NOTE)
    content["usd_per_mxn"] = round(1 / rate, 6)

    if args.amount is not None:
        to = QUOTE if args.currency == BASE else BASE
        converted = args.amount * rate if args.currency == BASE else args.amount / rate
        content["conversion"] = {
            "amount": args.amount,
            "currency": args.currency,
            "converted": round(converted, 2),
            "converted_currency": to,
        }
    content["note"] = " ".join(notes) if notes else ""
    if not content["note"]:
        del content["note"]
    meta["source"] = content["source"]
    return ToolOutput(content, meta=meta)


DESCRIPTION = (
    "The exchange rate between US dollars and Mexican pesos (MXN per USD), today's or "
    "a given day's. With amount, it also converts that amount to the other currency: "
    "use it instead of doing the arithmetic yourself. The result says whether the rate "
    "is live or the one stored in the company's books."
)


def external_tools() -> list[Tool]:
    return [Tool(name="fx_rate", description=DESCRIPTION, args=FxRateArgs, handler=fx_rate)]
