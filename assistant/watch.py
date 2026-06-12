"""Proactive watcher: a quiet check-in across tasks, calendar, and email.

Design, in three parts:

1. One-shot per launchd invocation. Like the briefing/insights jobs, this
   process runs exactly one check pass and exits; launchd's StartInterval
   (see scripts/install_macos.sh, com.aide.watch, every 1200 seconds)
   re-invokes it. There is no long-lived loop to leak memory or hold the
   model warm between passes. Setting ASSISTANT_WATCH_MINUTES=0 makes each
   invocation exit immediately, which disables the watcher without touching
   launchd.

2. The ALL-CLEAR contract. The prompt instructs the agent to reply with
   exactly "ALL-CLEAR" when nothing needs attention, and with 1-3 short
   lines otherwise. An ALL-CLEAR (or empty) reply produces no notification
   and no stdout noise, so 95% of passes are silent. Anything else notifies
   with the first line and prints the full reply to the job log.

3. Dedupe via a state file. The hash of the last non-clear reply persists
   in ASSISTANT_HOME/watch.state. If a new pass produces the identical
   reply, the notification is skipped (the reply is still printed to the
   log). Without this, one stale unread email would re-notify every 20
   minutes until it was read, which trains the user to ignore Aide.

The check itself is tool-gated: the agent only reports what due_tasks,
calendar tools (mcp__gcal__*), and email tools (mcp__gmail__*) actually
return, and skips connectors that are not configured. It never fabricates.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys

from . import config, engine, notify
from .log import get_logger

log = get_logger(__name__)

PROMPT = """Run my proactive check-in. Quietly check, using only tools that exist:
1. due_tasks (next 4 hours) and anything overdue.
2. If calendar tools (mcp__gcal__*) are available: events starting in the next 2 hours.
3. If email tools (mcp__gmail__*) are available: unread messages from the last 3 hours \
that look important (a person writing to me, a deadline, a confirmation; ignore \
newsletters/promos).
If NOTHING needs my attention, reply with exactly ALL-CLEAR and nothing else. \
Otherwise reply with 1-3 short lines, each one thing I'm missing, most urgent first. \
Never fabricate; only report what tools returned."""


def _watch_minutes() -> int:
    """Read ASSISTANT_WATCH_MINUTES defensively (default 20, 0 disables).

    This mirrors config._int_env (bad value falls back to the default,
    out-of-range value is clamped, warnings to stderr) but lives here
    because config.py has other work in flight and must not be edited.
    Read at call time, not import time, so tests and launchd restarts
    pick up the current environment.
    """
    raw = os.environ.get("ASSISTANT_WATCH_MINUTES")
    if raw is None or not raw.strip():
        return 20
    try:
        val = int(raw.strip())
    except ValueError:
        print(f"warning: ASSISTANT_WATCH_MINUTES={raw!r} is not an integer; using 20",
              file=sys.stderr)
        return 20
    if not (0 <= val <= 720):
        clamped = max(0, min(val, 720))
        print(f"warning: ASSISTANT_WATCH_MINUTES={val} outside [0, 720]; "
              f"clamped to {clamped}", file=sys.stderr)
        return clamped
    return val


async def main() -> None:
    """Run one watch pass: check connectors, notify only if something is found."""
    if _watch_minutes() == 0:
        print("watcher disabled")
        log.info("watch job skipped: ASSISTANT_WATCH_MINUTES=0")
        return

    ok, detail = config.backend_ready()
    if not ok:
        print(detail, file=sys.stderr)
        log.error("watch job aborted: backend not ready: %s", detail)
        raise SystemExit(1)

    log.info("watch job starting")
    eng = engine.make_engine(
        system_extra="\nYou are running unattended as a scheduled watch job.",
        unattended=True, max_turns=20, partial=False,
    )
    await eng.warm()

    final: list[str] = []
    try:
        async for ev in eng.run(PROMPT):
            if isinstance(ev, engine.Text):
                final.append(ev.text)
            elif isinstance(ev, engine.Done) and ev.status != "success":
                print(f"Watch run ended: {ev.status}", file=sys.stderr)
                log.warning("watch job ended with status %s", ev.status)
    finally:
        await eng.aclose()

    reply = (final[-1] if final else "").strip()
    if not reply or reply == "ALL-CLEAR":
        log.info("watch: all clear, nothing needs attention")
        return

    # Always surface the findings in the job log, even when deduped below.
    print(reply)

    # Dedupe: a stale unread email or an unmoved task would otherwise
    # re-notify on every 20-minute pass until acted on. If this pass found
    # exactly what the last one found, stay quiet and let the earlier
    # notification stand.
    state_path = config.ASSISTANT_HOME / "watch.state"
    digest = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    previous = state_path.read_text().strip() if state_path.is_file() else ""
    if digest == previous:
        log.info("watch: findings unchanged since last pass; notification skipped")
        return

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(digest)
    first_line = reply.splitlines()[0][:120]
    notify.notify("Aide: needs your attention", first_line)
    log.info("watch: notified: %s", first_line)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
