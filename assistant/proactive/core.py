"""The proactive engine's vocabulary: Insight, Check, and the shared Context.

An Insight is one thing worth surfacing. A Check produces Insights on some
cadence. The Context is handed to every Check during a cycle and lazily owns the
single shared engine, so deterministic checks cost nothing and LLM checks reuse
one model connection.
"""

from __future__ import annotations

import datetime as dt
from collections import namedtuple
from dataclasses import dataclass

from .. import config
from ..log import get_logger

log = get_logger(__name__)

# One timed calendar event, times normalized to local-naive for schedule math.
Event = namedtuple("Event", "start end title")

_UNSET = object()  # "calendar not fetched yet this cycle" vs. "fetched, none found"

_CAL_PROMPT = (
    "List my calendar events for {date} using your read-only calendar tool (never "
    "modify anything). Output ONE line per timed event, EXACTLY this format:\n"
    "START | END | TITLE\n"
    "where START and END are ISO-8601 datetimes copied verbatim from the tool "
    "result. Skip all-day events. Copy the times exactly; never infer or invent an "
    "event. If there are none, output exactly: NONE. No preamble, no other text."
)


def _parse_event_dt(s: str) -> dt.datetime | None:
    s = s.strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is not None:                       # normalize to local-naive
        d = d.astimezone().replace(tzinfo=None)
    return d


def _parse_events(text: str) -> list[Event]:
    """Parse `START | END | TITLE` lines into sorted Events. Malformed lines and
    'NONE' are skipped, so a garbled fetch yields [] (no false conflicts)."""
    out: list[Event] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-* ").strip()
        if not line or line.upper() == "NONE":
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        start, end = _parse_event_dt(parts[0]), _parse_event_dt(parts[1])
        if not start or not end:
            continue
        out.append(Event(start, end, parts[2][:120]))
    out.sort(key=lambda e: e.start)
    return out

FEED = "feed"        # lands silently in the Routines feed
NOTIFY = "notify"    # also pings macOS (time-sensitive only)

# Cadences a Check can declare. The runner decides which are due this cycle.
CYCLE = "cycle"      # every run (~15 min)
HOURLY = "hourly"
DAILY = "daily"
WEEKLY = "weekly"
ON_WAKE = "on_wake"  # only when the user just returned to the Mac


@dataclass
class Insight:
    """One surfaced item. `key` is the stable dedupe identity: the same key
    within the dedupe window is shown once, not every cycle."""
    key: str
    category: str
    title: str
    body: str = ""
    urgency: str = FEED
    action_prompt: str = ""   # a chat turn the user can run with one tap
    source: str = ""          # provenance, the "why am I seeing this" line


class Check:
    """Base class for a proactive check. Subclasses set `name`/`category`/
    `cadence` and implement `run`. Override `gate` for a cheap precondition
    (connector present, feature enabled, in a time window); a False gate skips
    the check without building anything."""

    name: str = "check"
    category: str = "general"
    cadence: str = CYCLE

    def gate(self, ctx: "Context") -> bool:  # noqa: D401
        return True

    def run(self, ctx: "Context") -> list[Insight]:
        raise NotImplementedError


class Context:
    """Per-cycle state shared across checks. Builds the engine lazily: the first
    LLM check to call `ask` pays for it, deterministic checks never do."""

    def __init__(self, now: dt.datetime | None = None,
                 returned_from_away: bool = False) -> None:
        self.now = now or _local_now()
        self.returned_from_away = returned_from_away
        self._engine = None
        self._events = _UNSET   # today's calendar, fetched once and shared

    async def ask(self, prompt: str, *, max_turns: int = 12) -> str:
        """Run one unattended engine turn and return its final text. The engine
        is built once per cycle and reused. Read-only by policy: the prompt must
        not ask the model to modify anything (callers enforce this)."""
        from .. import engine
        if self._engine is None:
            self._engine = engine.make_engine(
                system_extra=("\nYou are running unattended as a proactive background "
                              "check. Email and calendar are READ-ONLY: never send, "
                              "label, archive, organize, or draft into them. Do not ask "
                              "questions. Be terse."),
                unattended=True, max_turns=max_turns, partial=False,
            )
            await self._engine.warm()
        out: list[str] = []
        async for ev in self._engine.run(prompt):
            if isinstance(ev, engine.Text):
                out.append(ev.text)
        return out[-1].strip() if out else ""

    async def calendar_today(self) -> list[Event]:
        """Today's timed events, fetched once per cycle via the read-only calendar
        connector and shared across checks. Returns [] when connectors are off or
        the fetch fails, so callers never reason over half-data."""
        if self._events is not _UNSET:
            return self._events
        self._events = []
        if not config.connectors_available():
            return self._events
        try:
            raw = await self.ask(
                _CAL_PROMPT.format(date=self.now.date().isoformat()), max_turns=8)
            self._events = _parse_events(raw)
        except Exception:  # noqa: BLE001 - a failed fetch must not sink the cycle
            log.warning("calendar_today fetch failed", exc_info=True)
        return self._events

    async def aclose(self) -> None:
        if self._engine is not None:
            try:
                await self._engine.aclose()
            except Exception:  # noqa: BLE001 - teardown best effort
                log.warning("proactive engine aclose failed", exc_info=True)
            self._engine = None

    @property
    def used_engine(self) -> bool:
        return self._engine is not None


def _local_now() -> dt.datetime:
    return dt.datetime.now()
