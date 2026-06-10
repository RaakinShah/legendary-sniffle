"""Nightly insights — distill the day's ambient recall into durable memory.

Run on a schedule (launchd, evenings). Reviews the recall timeline, files what
matters into long-term memory, writes an evening digest to
~/.assistant/insights/YYYY-MM-DD.md, and sends a notification.
"""

from __future__ import annotations

import asyncio

from . import config
from .scheduled import run_job

PROMPT_TEMPLATE = """Run my end-of-day distillation for {date}.

1. Pull today's activity with recall_timeline (since_hours=18). Where an entry looks \
significant but vague, use recall_search to fill in specifics.
2. Distill what matters into long-term memory:
   - new/updated projects or commitments -> projects.md (Edit/Write)
   - durable personal facts or preferences you learned -> remember tool
   - a 3-6 line summary of the day -> journal tool
   File, don't hoard: skip routine noise (brief app switches, idle time).
3. Check list_tasks: if today's activity shows something was finished, complete it; \
if activity revealed a new obligation (a promised reply, a form to submit), add it.

Then write a short evening digest to {path} with the Write tool:
# Evening digest — {date}
- **What happened** (3-5 bullets, from the timeline)
- **Loose ends** (things started but unfinished, with where they live)
- **Tomorrow's setup** (1-2 concrete suggestions)

Finally output the digest text as your final message."""


async def main() -> None:
    await run_job(
        label="insights",
        out_dir=config.INSIGHTS_DIR,
        prompt_template=PROMPT_TEMPLATE,
        max_turns=40,
        notify_title="Evening digest ready",
        notify_message="Today is distilled — loose ends inside.",
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
