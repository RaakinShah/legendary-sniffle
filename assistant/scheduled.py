"""Shared runner for unattended scheduled jobs (morning briefing, evening insights)."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage, query

from . import config, notify
from .agent import build_options


async def run_job(
    *,
    label: str,
    out_dir: Path,
    prompt_template: str,
    max_turns: int,
    notify_title: str,
    notify_message: str,
) -> None:
    """Run a one-shot scheduled agent job: render the prompt, stream it, save, notify.

    The prompt template may reference {date} and {path}; notify_message may use {path}.
    """
    if not config.auth_available():
        print(config.AUTH_HELP, file=sys.stderr)
        raise SystemExit(1)

    today = dt.date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today}.md"
    prompt = prompt_template.format(date=today, path=out_path)

    options = build_options(
        extra_system=f"\nYou are running unattended as a scheduled {label} job — do not ask questions.",
        max_turns=max_turns,
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            if message.subtype == "success" and message.result:
                print(message.result)
            else:
                print(f"{label.capitalize()} run ended: {message.subtype}", file=sys.stderr)

    if out_path.exists():
        print(f"\n(saved to {out_path})", file=sys.stderr)
        notify.notify(notify_title, notify_message.format(path=out_path))


def main_runner(coro_factory) -> None:
    """Wrap an async main() as a console-script entry point."""
    asyncio.run(coro_factory())
