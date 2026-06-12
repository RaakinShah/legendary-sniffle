"""Shared runner for unattended scheduled jobs (morning briefing, evening insights).

Backend-agnostic: drives an Engine (local Ollama by default, Claude opt-in).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

from . import config, engine, notify
from .log import get_logger

log = get_logger(__name__)


async def run_job(
    *,
    label: str,
    out_dir: Path,
    prompt_template: str,
    max_turns: int,
    notify_title: str,
    notify_message: str,
    model: str | None = None,
    effort=config.UNSET,
) -> None:
    """Run a one-shot scheduled agent job: render the prompt, run it, save, notify.

    The prompt template may reference {date}, {path}, and {memory_dir};
    notify_message may use {path}. `model`/`effort` let a job run on a stronger
    Claude model than the everyday default (memory consolidation does this).
    """
    ok, detail = config.backend_ready()
    if not ok:
        print(detail, file=sys.stderr)
        log.error("%s job aborted: backend not ready: %s", label, detail)
        raise SystemExit(1)

    today = dt.date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today}.md"
    prompt = prompt_template.format(date=today, path=out_path,
                                    memory_dir=config.MEMORY_DIR)

    log.info("%s job starting (model=%s)", label, model or "default")
    eng = engine.make_engine(
        system_extra=f"\nYou are running unattended as a scheduled {label} job.",
        unattended=True, max_turns=max_turns, partial=False,
        model=model, effort=effort,
    )
    await eng.warm()

    final: list[str] = []
    try:
        async for ev in eng.run(prompt):
            if isinstance(ev, engine.Text):
                final.append(ev.text)
            elif isinstance(ev, engine.Done) and ev.status != "success":
                print(f"{label.capitalize()} run ended: {ev.status}", file=sys.stderr)
                log.warning("%s job ended with status %s", label, ev.status)
    finally:
        await eng.aclose()

    if final:
        print(final[-1])

    if out_path.exists():
        print(f"\n(saved to {out_path})", file=sys.stderr)
        notify.notify(notify_title, notify_message.format(path=out_path))


def main_runner(coro_factory) -> None:
    """Wrap an async main() as a console-script entry point."""
    asyncio.run(coro_factory())
