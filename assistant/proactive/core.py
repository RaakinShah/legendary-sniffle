"""The proactive engine's vocabulary: Insight, Check, and the shared Context.

An Insight is one thing worth surfacing. A Check produces Insights on some
cadence. The Context is handed to every Check during a cycle and lazily owns the
single shared engine, so deterministic checks cost nothing and LLM checks reuse
one model connection.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..log import get_logger

log = get_logger(__name__)

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
