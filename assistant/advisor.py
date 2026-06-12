"""The Haiku advisor: a strong cloud consultant for the local base model.

The local Ollama model drives every turn. When it knows it's out of its depth it
calls the `ask_advisor` tool (see toolkit.py), which lands here: a single,
stateless Claude query with no tools and a tight system prompt, billed to the
same Claude credentials as the "claude" backend. The separate auto-rescue path
(engine.py) instead hands the whole stuck turn to a full Haiku agent.

Everything here fails soft: if there's no Claude auth or the network is down, the
consult returns a short explanation string (for the tool) rather than raising, so
the local model can carry on alone.
"""

from __future__ import annotations

from . import config
from .log import get_logger

log = get_logger(__name__)

ADVISOR_SYSTEM = (
    "You are a precise expert advisor. You are being consulted by a smaller local "
    "AI assistant running on the user's Mac that has gotten stuck or is unsure. "
    "Answer the question directly and correctly. Lead with the answer, then give "
    "only the reasoning or steps that are actually useful. Be concrete and concise. "
    "Your reply is read by the smaller model (and may be shown to the user), so do "
    "not hedge, do not ask clarifying questions, and do not mention that you are an "
    "advisor — just give the best answer you can with the information provided."
)

# Cap the advisor's own output so one consult can't balloon context for the small
# local model that has to read it back.
_MAX_REPLY_CHARS = 4000


def available() -> bool:
    """True when a consult can actually reach Claude (auth + the SDK present)."""
    if not config.advisor_available():
        return False
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return True


async def consult(question: str, context: str = "", *,
                  model: str | None = None, effort=config.UNSET) -> str:
    """Ask a stronger Claude model one question. Returns its answer, or a short
    '(advisor unavailable: …)' note the caller can read and move past. Never raises.

    `model` defaults to the Haiku advisor (ASSISTANT_ADVISOR_MODEL); the
    `think_harder` tool passes Sonnet/Opus with a real effort level. Gated on
    Claude credentials being present (auth), so it works on either backend."""
    question = (question or "").strip()
    if not question:
        return "(advisor unavailable: no question was provided)"
    if not config.auth_available():
        return ("(advisor unavailable: no Claude credentials "
                "— answer from your own knowledge and tools)")
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )
    except Exception as exc:  # noqa: BLE001 - SDK missing is just "no advisor"
        log.debug("consult: SDK unavailable: %s", exc)
        return f"(advisor unavailable: {exc})"

    prompt = question if not context.strip() else f"{question}\n\nContext:\n{context.strip()}"
    opts: dict = {
        "system_prompt": ADVISOR_SYSTEM,
        "model": model or config.ADVISOR_MODEL,
        "max_turns": 1,
    }
    eff = None if effort is config.UNSET else effort
    if eff is not None:                          # omit for Haiku (no Opus effort)
        opts["effort"] = eff
    options = ClaudeAgentOptions(**opts)
    parts: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
    except Exception as exc:  # noqa: BLE001 - offline / auth / rate limit: degrade
        from .util import redact
        log.warning("consult to %s failed: %s", opts["model"], redact(str(exc)))
        return f"(advisor unavailable: {redact(str(exc))})"

    answer = "".join(parts).strip()
    if not answer:
        return "(advisor returned no answer — proceed on your own)"
    if len(answer) > _MAX_REPLY_CHARS:
        answer = answer[:_MAX_REPLY_CHARS].rstrip() + "…"
    return answer
