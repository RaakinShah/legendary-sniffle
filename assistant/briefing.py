"""Proactive daily briefing — run on a schedule (cron/launchd) or manually.

Generates a short morning brief from tasks, memory, and (if connected)
calendar/email, saves it to ~/.assistant/briefings/YYYY-MM-DD.md, and prints it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

from claude_agent_sdk import ResultMessage, query

from . import config, notify
from .agent import build_options

PROMPT_TEMPLATE = """Generate my daily briefing for {date}.

1. Check due_tasks (next 48 hours) and list_tasks for what's on my plate.
2. If calendar tools are available, pull today's events. If email tools are available, \
scan for unread messages from the last day that look important. If neither is connected, \
skip silently — do not mention missing integrations.
3. Review your memory for anything time-relevant (projects, commitments).

Then write a brief, scannable markdown briefing with sections only when they have content:
# Briefing — {date}
- **Top priorities** (max 3)
- **Due / overdue**
- **Calendar** (if connected)
- **Inbox highlights** (if connected)
- **A suggestion** — one proactive, genuinely useful idea for the day

Save it with the Write tool to {path}, then also output the full briefing text as your \
final message."""


async def main() -> None:
    if not config.auth_available():
        print(config.AUTH_HELP, file=sys.stderr)
        raise SystemExit(1)

    today = dt.date.today().isoformat()
    out_path = config.BRIEFINGS_DIR / f"{today}.md"
    prompt = PROMPT_TEMPLATE.format(date=today, path=out_path)

    options = build_options(
        extra_system="\nYou are running unattended as a scheduled briefing job — do not ask questions.",
        max_turns=30,
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            if message.subtype == "success" and message.result:
                print(message.result)
            else:
                print(f"Briefing run ended: {message.subtype}", file=sys.stderr)

    if out_path.exists():
        print(f"\n(saved to {out_path})", file=sys.stderr)
        notify.notify("Daily briefing ready", f"Saved to {out_path}")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
