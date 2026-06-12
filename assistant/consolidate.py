"""Weekly memory consolidation — keep long-term memory sharp instead of sprawling.

Memory files only ever grow during normal use: `remember` appends, corrections
pile up next to the facts they correct, and inbox.md collects loose items that
never get filed. Left alone, the wholesale memory dump in the system prompt
slowly fills with duplicates and stale entries, and the 8KB-per-file cap starts
truncating what matters.

This job runs on a schedule (launchd, weekly) and has the agent rewrite the
memory files in place: file inbox items where they belong, merge duplicates,
revise or drop what's no longer true, and trim dead weight. It runs on the
stronger escalation model when available — restructuring the assistant's
long-term knowledge is exactly the work that deserves the better brain.

A short report goes to ~/.assistant/consolidation/YYYY-MM-DD.md.
"""

from __future__ import annotations

import asyncio

from . import config
from .scheduled import run_job

PROMPT_TEMPLATE = """Run my weekly memory consolidation for {date}.

Your long-term memory lives in {memory_dir} as four markdown files: profile.md
(who I am), preferences.md (how I like things done), projects.md (current work),
and inbox.md (unfiled facts).

1. Read all four files in full.
2. Rewrite them in place with your file tools, applying these rules:
   - File every inbox.md item into the right file (profile/preferences/projects); \
afterwards inbox.md should hold only genuinely unclassifiable leftovers.
   - Merge duplicates and near-duplicates into one line; keep the most recent date.
   - Where a newer fact contradicts an older one, keep the newer fact and drop the old.
   - Projects that are clearly finished or abandoned: compress to one summary line \
or remove them.
   - Keep each file comfortably under 6000 characters; trim the least useful detail first.
   - NEVER invent, embellish, or infer new facts. Only reorganize and condense what \
is already written. When unsure whether something is stale, keep it.
3. Re-read each file after writing to confirm the result is clean, well-formed \
markdown with the original section headers intact.

Then write a short report to the file {path}:
# Memory consolidation — {date}
- **Filed** (inbox items moved, with destination counts)
- **Merged/updated** (duplicates collapsed, facts revised)
- **Trimmed** (what was removed and why)
- **Flagged** (anything that looked stale but was kept, for me to confirm)

Finally output the report text as your final message."""


async def main() -> None:
    # Consolidation rewrites the assistant's long-term knowledge, so it gets the
    # stronger escalation model when reachable; otherwise the everyday default.
    model = config.ESCALATE_MODEL if config.escalation_available() else None
    effort = config.ESCALATE_EFFORT if config.escalation_available() else config.UNSET
    await run_job(
        label="consolidation",
        out_dir=config.ASSISTANT_HOME / "consolidation",
        prompt_template=PROMPT_TEMPLATE,
        max_turns=40,
        notify_title="Memory consolidated",
        notify_message="Long-term memory was tidied — report inside.",
        model=model,
        effort=effort,
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
