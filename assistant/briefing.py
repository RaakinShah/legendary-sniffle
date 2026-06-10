"""Proactive daily briefing — run on a schedule (cron/launchd) or manually.

Generates a short morning brief from tasks, memory, and (if connected)
calendar/email, saves it to ~/.assistant/briefings/YYYY-MM-DD.md, and prints it.
"""

from __future__ import annotations

import asyncio

from . import config
from .scheduled import run_job

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
    await run_job(
        label="briefing",
        out_dir=config.BRIEFINGS_DIR,
        prompt_template=PROMPT_TEMPLATE,
        max_turns=30,
        notify_title="Daily briefing ready",
        notify_message="Saved to {path}",
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
