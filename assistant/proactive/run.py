"""The single proactive runner: `assistant-proactive`.

launchd fires it every ~15 minutes. One cycle: pick the checks due this run,
build ONE shared engine only if an LLM check actually needs it, collect Insights,
dedupe them into the feed store, and ping macOS for the few that are urgent and
fall outside quiet hours. Then exit, freeing the engine. This is the spine that
keeps twenty features at one short-lived process, not twenty.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys

from .. import config, notify
from ..log import get_logger
from . import checks, store
from .core import CYCLE, DAILY, HOURLY, NOTIFY, ON_WAKE, WEEKLY, Context

log = get_logger(__name__)

# Quiet hours: no pings (items still land silently in the feed). Configurable.
QUIET_START = config._int_env("ASSISTANT_QUIET_START", 23, 0, 23)
QUIET_END = config._int_env("ASSISTANT_QUIET_END", 8, 0, 23)
MAX_PINGS_PER_CYCLE = 2

# Where the last run of each cadence is recorded, so daily/weekly checks fire
# about once per day/week regardless of the 15-min cycle.
_STATE = lambda: config.ASSISTANT_HOME / "proactive.state"


def _in_quiet_hours(now: dt.datetime) -> bool:
    h = now.hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END   # window wraps midnight


def _load_state() -> dict:
    import json
    try:
        return json.loads(_STATE().read_text())
    except Exception:  # noqa: BLE001 - missing/corrupt state just means "run everything"
        return {}


def _save_state(state: dict) -> None:
    import json
    try:
        _STATE().write_text(json.dumps(state))
    except Exception:  # noqa: BLE001
        log.warning("could not save proactive state", exc_info=True)


def _is_due(check, state: dict, now: dt.datetime) -> bool:
    """Cadence gating. CYCLE always runs; HOURLY/DAILY/WEEKLY run if enough time
    has elapsed since the last run recorded in state. ON_WAKE runs only when the
    user just returned (handled by the check's own gate, but skipped here unless
    the wake signal is present)."""
    if check.cadence == CYCLE:
        return True
    if check.cadence == ON_WAKE:
        return True   # the check's gate() decides using ctx.returned_from_away
    last = state.get(check.name)
    if not last:
        return True
    try:
        elapsed = (now - dt.datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return True
    window = {HOURLY: 3600, DAILY: 86400, WEEKLY: 7 * 86400}.get(check.cadence, 0)
    return elapsed >= window * 0.9   # a little slack so a ~daily cron still fires


def _returned_from_away() -> bool:
    """Best-effort: did the user just come back? True if the newest activity row
    is recent but preceded by a gap. Off-platform / no recall -> False."""
    if sys.platform != "darwin" or not config.RECALL:
        return False
    try:
        from contextlib import closing
        from .. import observer
        if not observer._db_path().exists():
            return False
        with closing(observer._conn()) as con:
            rows = con.execute(
                "SELECT ts FROM activity ORDER BY ts DESC LIMIT 2").fetchall()
        if len(rows) < 2:
            return False
        newest = dt.datetime.fromisoformat(rows[0][0])
        prev = dt.datetime.fromisoformat(rows[1][0])
        # Newest sample is fresh (<5 min) but the gap before it was long (>25 min).
        fresh = (dt.datetime.now() - newest).total_seconds() < 300
        gap = (newest - prev).total_seconds() > 1500
        return fresh and gap
    except Exception:  # noqa: BLE001
        return False


async def main() -> None:
    ok, detail = config.backend_ready()
    if not ok:
        log.error("proactive run aborted: %s", detail)
        return

    now = dt.datetime.now()
    state = _load_state()
    ctx = Context(now=now, returned_from_away=_returned_from_away())

    insights = []
    for check in checks.active_checks():
        if not _is_due(check, state, now):
            continue
        try:
            if not check.gate(ctx):
                continue
            if hasattr(check, "arun"):
                found = await check.arun(ctx)
            else:
                found = check.run(ctx)
            insights.extend(found or [])
            state[check.name] = now.isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001 - one bad check never sinks the cycle
            log.exception("proactive check %s failed", check.name)

    await ctx.aclose()
    store.prune()

    fresh = [ins for ins in insights if store.add(ins)]
    log.info("proactive cycle: %d insights, %d new (engine=%s)",
             len(insights), len(fresh), ctx.used_engine)

    pings = [ins for ins in fresh if ins.urgency == NOTIFY]
    if pings and not _in_quiet_hours(now):
        for ins in pings[:MAX_PINGS_PER_CYCLE]:
            notify.notify(f"Aide: {ins.title}", (ins.body or "")[:120])

    _save_state(state)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
