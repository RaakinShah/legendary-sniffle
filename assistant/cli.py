"""Interactive chat loop. Run with `assistant` after `pip install -e .`."""

from __future__ import annotations

import asyncio
import os
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from . import config
from .agent import build_options

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

BANNER = f"""{BOLD}{config.ASSISTANT_NAME}{RESET} — your personal assistant (model: {config.MODEL})
Data lives in {config.ASSISTANT_HOME}. Type your message, or /quit to exit.
"""


def _check_auth() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    print(
        "No ANTHROPIC_API_KEY found.\n"
        "  1. Get a key at https://console.anthropic.com (API Keys)\n"
        "  2. Copy .env.example to .env and paste your key in\n"
        "  3. Run `assistant` again",
        file=sys.stderr,
    )
    return False


async def _respond(client: ClaudeSDKClient) -> None:
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
                elif isinstance(block, ToolUseBlock):
                    print(f"{DIM}  [{block.name}]{RESET}")
        elif isinstance(message, ResultMessage):
            if message.subtype != "success":
                print(f"{DIM}(stopped: {message.subtype}){RESET}")


async def main() -> None:
    if not _check_auth():
        raise SystemExit(1)

    print(BANNER)
    async with ClaudeSDKClient(options=build_options()) as client:
        # Kick off with a proactive greeting that surfaces anything due.
        await client.query(
            "Session started. Greet me briefly; if anything is overdue or due today, "
            "surface it in one or two lines. Then wait for my input."
        )
        await _respond(client)

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
            await client.query(user_input)
            print()
            await _respond(client)


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
