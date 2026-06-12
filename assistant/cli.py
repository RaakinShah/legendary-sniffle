"""Interactive chat loop. Run with `assistant` after `pip install -e .`.

Backend-agnostic: drives an Engine (local Ollama by default, Claude if
ASSISTANT_BACKEND=claude) and renders its normalized event stream.
"""

from __future__ import annotations

import asyncio
import sys

from . import config, engine

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _model_label() -> str:
    return config.OLLAMA_MODEL if config.BACKEND != "claude" else config.MODEL


BANNER = f"""{BOLD}{config.ASSISTANT_NAME}{RESET} — your personal assistant \
({config.BACKEND}: {_model_label()})
Data lives in {config.ASSISTANT_HOME}. /context shows what I'm working from; /quit exits.
"""


def _print_context() -> None:
    """Render the working-memory snapshot (same data as the GUI's ⌘I panel)."""
    from . import agent
    snap = agent.context_snapshot()
    print(f"\n{BOLD}Brain{RESET}      {snap['brain']}")
    recall = "on" if snap["recall"] else "paused"
    print(f"{BOLD}Right now{RESET}  {snap['context'] or '(no ambient context)'}  {DIM}[recall {recall}]{RESET}")
    print(f"{BOLD}Memory{RESET}     {DIM}({config.MEMORY_DIR}){RESET}")
    any_mem = False
    for name, body in snap["memory"].items():
        lines = [l.strip() for l in body.splitlines()
                 if l.strip() and not l.startswith("#")
                 and not (l.strip().startswith("(") and l.strip().endswith(")"))]
        if lines:
            any_mem = True
            print(f"  {name}:")
            for l in lines:
                print(f"    {l}")
    if not any_mem:
        print(f"  {DIM}(nothing saved yet){RESET}")
    due, open_ = snap["tasks"]["due"], snap["tasks"]["open"]
    print(f"{BOLD}Tasks{RESET}")
    for t in due:
        print(f"  due soon: {t}")
    for t in (x for x in open_ if x not in due):
        print(f"  open:     {t}")
    if not due and not open_:
        print(f"  {DIM}(no open tasks){RESET}")


def _check_backend() -> bool:
    ok, detail = config.backend_ready()
    if not ok:
        print(detail, file=sys.stderr)
    return ok


async def _drive(eng, text: str) -> None:
    """Stream one turn to stdout. Tokens print live; tool calls dim; a finalized
    text block prints only if it wasn't already streamed (Claude non-partial)."""
    streamed = False
    async for ev in eng.run(text):
        if isinstance(ev, engine.Delta):
            streamed = True
            print(ev.text, end="", flush=True)
        elif isinstance(ev, engine.ToolCall):
            print(f"\n{DIM}  [{ev.name}]{RESET}")
            streamed = False
        elif isinstance(ev, engine.Text):
            if not streamed:
                print(ev.text)
            streamed = False
        elif isinstance(ev, engine.Done):
            print()
            if ev.status != "success":
                print(f"{DIM}(stopped: {ev.status}){RESET}")


async def main() -> None:
    if not _check_backend():
        raise SystemExit(1)

    print(BANNER)
    eng = engine.make_engine(partial=False)
    if config.BACKEND != "claude":
        print(f"{DIM}warming {config.OLLAMA_MODEL}…{RESET}", flush=True)
    await eng.warm()

    # Kick off with a proactive greeting grounded in the real task list.
    from . import agent
    await _drive(eng, agent.greeting_prompt())

    try:
        while True:
            try:
                user_input = input(f"\n{BOLD}you>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye!")
                break
            if not user_input:
                continue
            if user_input.lower() in ("/quit", "/exit", "/q"):
                print("bye!")
                break
            if user_input.lower() == "/context":
                _print_context()
                continue
            print()
            await _drive(eng, user_input)
    finally:
        await eng.aclose()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
