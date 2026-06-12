"""Pluggable agent engine: one interface, two brains.

The rest of the app (GUI bridge, CLI, scheduled jobs) drives an Engine and reacts
to a small set of normalized events, so it never cares whether the brain is a
local Ollama model or the Claude Agent SDK.

Events:
  Delta(text)   streaming token of the in-progress reply
  Text(text)    a finalized assistant text block (render as markdown)
  ToolCall(name) a tool is being invoked
  Done(status, session_id)  the turn finished

Pick the backend with ASSISTANT_BACKEND (see config.BACKEND for the default).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from . import config
from .log import get_logger

log = get_logger(__name__)


@dataclass
class Delta:
    text: str


@dataclass
class Text:
    text: str


@dataclass
class ToolCall:
    name: str


@dataclass
class Done:
    status: str = "success"
    session_id: str | None = None


# --- local (Ollama) engine ---------------------------------------------------

def _calls_signature(tool_calls: list[dict]) -> str:
    """A stable signature of one step's tool calls, used to notice when the local
    model is stuck issuing the same call(s) round after round."""
    items = []
    for tc in tool_calls:
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments", {})
        try:
            args_s = json.dumps(args, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 - signatures are best-effort
            args_s = str(args)
        items.append((fn.get("name", ""), args_s))
    return repr(sorted(items))


class OllamaEngine:
    """Runs the agent loop against a local Ollama model: stream tokens, dispatch
    tool calls, feed results back, repeat until the model stops calling tools."""

    def __init__(self, system: str, resume_messages: list[dict] | None = None,
                 mac: bool = True) -> None:
        from . import toolkit
        from .ollama import OllamaClient

        self.client = OllamaClient()
        self.specs, self.dispatch = toolkit.build_toolset(mac=mac)
        self.messages: list[dict] = [{"role": "system", "content": system}]
        if resume_messages:
            self.messages.extend(resume_messages)
        self.think = config.wants_thinking(self.client.model)
        self.max_steps = config.OLLAMA_MAX_STEPS
        self.session_id: str | None = None

    async def warm(self) -> None:
        await self.client.warm()

    async def run(self, user_text: str):
        from .ollama import OllamaError

        self.messages.append({"role": "user", "content": user_text})
        seen: dict[str, int] = {}        # repeated tool-call signatures (loop guard)

        for step in range(1, self.max_steps + 1):
            self._trim()        # keep history within the context window
            assistant: dict = {"role": "assistant", "content": ""}
            tool_calls: list[dict] = []
            try:
                async for chunk in self.client.chat(
                    self.messages, self.specs, stream=True,
                    think=self.think or None,
                ):
                    msg = chunk.get("message", {}) or {}
                    piece = msg.get("content")
                    if piece:
                        assistant["content"] += piece
                        yield Delta(piece)
                    if msg.get("tool_calls"):
                        tool_calls.extend(msg["tool_calls"])
                    if chunk.get("done"):
                        break
            except OllamaError as exc:
                # The local stack itself failed — try to hand the turn to the advisor.
                async for ev in self._maybe_rescue(user_text, f"hit a local error ({exc})"):
                    yield ev
                    if isinstance(ev, Done):
                        return
                yield Text(f"⚠️ {exc}")
                yield Done("error")
                return

            if tool_calls:
                assistant["tool_calls"] = tool_calls
            if assistant["content"].strip():
                yield Text(assistant["content"])
            self.messages.append(assistant)

            if not tool_calls:
                yield Done("success")
                return

            if step == self.max_steps:
                async for ev in self._maybe_rescue(user_text, "reached the tool-step limit"):
                    yield ev
                    if isinstance(ev, Done):
                        return
                yield Text("(Stopped — reached the tool-step limit for one turn.)")
                yield Done("error")
                return

            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                yield ToolCall(name)
                handler = self.dispatch.get(name)
                if handler is None:
                    result = f"Error: unknown tool '{name}'."
                else:
                    try:
                        result = await handler(args or {})
                    except Exception as exc:  # noqa: BLE001 - surface to the model, don't crash
                        result = f"Tool '{name}' raised: {exc}"
                self.messages.append({"role": "tool", "content": result, "tool_name": name})

            # Loop guard: a model that keeps issuing the same call(s) every round is
            # stuck. After ADVISOR_LOOP_LIMIT repeats, escalate rather than grind to
            # the step cap.
            sig = _calls_signature(tool_calls)
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] >= config.ADVISOR_LOOP_LIMIT:
                async for ev in self._maybe_rescue(user_text, "kept repeating the same tool call"):
                    yield ev
                    if isinstance(ev, Done):
                        return
                # No advisor reachable: reset so we don't retrigger every round and
                # let the loop run to its natural step cap.
                seen[sig] = 0

    def _trim(self) -> None:
        """Keep the system message plus the most recent turns within a rough char
        budget tied to the context window, so a long session can't silently push
        the system prompt (and its anti-fabrication rules) out of the model's
        context — Ollama would otherwise drop the oldest content itself.

        The newest complete turn (from the last user message on) always survives,
        whatever its size; older turns are kept newest-first while they fit, and
        the cut lands on a user-message boundary so the model never sees an
        orphaned assistant/tool half-turn."""
        if len(self.messages) <= 2:
            return
        budget = config.OLLAMA_NUM_CTX * 3      # ~3-4 chars/token; leave room for the reply
        system, rest = self.messages[0], self.messages[1:]

        def clen(m: dict) -> int:
            return len(str(m.get("content", "") or ""))

        last_user = max((i for i, m in enumerate(rest) if m.get("role") == "user"),
                        default=0)
        tail = rest[last_user:]                  # the in-progress turn: always kept
        total = len(str(system.get("content", "") or "")) + sum(clen(m) for m in tail)
        kept: list[dict] = []
        for m in reversed(rest[:last_user]):     # older turns, newest first
            if total + clen(m) > budget:
                break
            total += clen(m)
            kept.append(m)
        kept.reverse()
        while kept and kept[0].get("role") != "user":
            kept.pop(0)
        self.messages = [system] + kept + tail

    def _recent_transcript(self, limit: int = 8) -> str:
        """A compact transcript of the last few turns, to brief the advisor."""
        lines = []
        for m in self.messages[1:][-limit:]:        # skip the system message
            role = m.get("role", "")
            content = str(m.get("content", "") or "").strip()
            if not content:
                continue
            if role == "tool":
                content = f"[tool result] {content[:300]}"
            lines.append(f"{role}: {content[:600]}")
        return "\n".join(lines)

    async def _maybe_rescue(self, user_text: str, reason: str):
        """If auto-rescue is on and the advisor is reachable, hand the whole stuck
        turn to a Haiku agent (full tools) and stream its answer through to a final
        Done. The advisor gets the recent conversation as context, and its reply is
        recorded in local history so the next turn keeps continuity. Yields nothing
        when no rescue is possible, so the caller falls back to its stop behavior."""
        if not config.ADVISOR_RESCUE:
            return
        from . import advisor
        if not advisor.available():
            return
        yield ToolCall("advisor")
        yield Delta(f"\n_(local model {reason} — bringing in the Haiku advisor)_\n\n")
        transcript = self._recent_transcript()
        extra = ("\nYou are stepping in for a smaller local assistant that got stuck. "
                 "Recent conversation for context:\n" + transcript) if transcript else ""
        sub = ClaudeEngine(system_extra=extra, model=config.ADVISOR_MODEL,
                           effort=None, partial=True)
        answer: list[str] = []
        try:
            async for ev in sub.run(user_text):
                if isinstance(ev, Text):
                    answer.append(ev.text)
                if isinstance(ev, Done) and answer:
                    # Record the rescued reply BEFORE yielding Done — the caller
                    # returns on Done, which aborts this generator at that yield,
                    # so anything after the loop would never run.
                    self.messages.append(
                        {"role": "assistant", "content": "\n\n".join(answer)})
                yield ev
        finally:
            await sub.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()


# --- Claude (Agent SDK) engine ----------------------------------------------

class ClaudeEngine:
    """Adapts the Claude Agent SDK to the same event stream.

    `model`/`effort` override the configured defaults; the Haiku auto-rescue
    constructs one with model=ADVISOR_MODEL and effort=None (omit). The
    unspecified/None/value handling lives in build_options — the one place
    the effort decision is made."""

    def __init__(self, system_extra: str = "", resume_session: str | None = None,
                 max_turns: int | None = None, partial: bool = True,
                 model: str | None = None, effort=config.UNSET) -> None:
        self._extra = system_extra
        self._max_turns = max_turns
        self._partial = partial
        self._model = model
        self._effort = effort
        self.client = None
        self.session_id = resume_session

    async def _ready(self):
        if self.client is None:
            from claude_agent_sdk import ClaudeSDKClient

            from .agent import build_options
            self.client = ClaudeSDKClient(options=build_options(
                extra_system=self._extra, max_turns=self._max_turns,
                partial_messages=self._partial, resume=self.session_id,
                model=self._model, effort=self._effort,
            ))
            await self.client.connect()
        return self.client

    async def warm(self) -> None:
        await self._ready()

    async def run(self, user_text: str):
        from claude_agent_sdk import (
            AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock,
        )
        try:
            client = await self._ready()
            await client.query(user_text)
            async for m in client.receive_response():
                if isinstance(m, StreamEvent):
                    ev = m.event or {}
                    if ev.get("type") == "content_block_delta":
                        d = ev.get("delta", {})
                        if d.get("type") == "text_delta" and d.get("text"):
                            yield Delta(d["text"])
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock):
                            yield Text(b.text)
                        elif isinstance(b, ToolUseBlock):
                            yield ToolCall(b.name)
                elif isinstance(m, ResultMessage):
                    if m.session_id:
                        self.session_id = m.session_id
                    yield Done("success" if m.subtype == "success" else m.subtype, m.session_id)
        except Exception as exc:  # noqa: BLE001
            from .util import redact
            log.warning("Claude turn failed on %s: %s",
                        self._model or config.MODEL, redact(str(exc)))
            await self.aclose()
            async for ev in self._maybe_escalate(user_text, exc):
                yield ev
                if isinstance(ev, Done):
                    return
            yield Text(f"⚠️ {redact(str(exc))}")
            yield Done("error")

    async def _maybe_escalate(self, user_text: str, exc: Exception):
        """One-shot retry of a failed turn on the stronger escalation model.

        Only the default-model engine escalates (sub-engines always pass an
        explicit model, so a failed escalation can't recurse). The retried turn
        runs as a fresh session; the next normal turn resumes the original
        conversation via session_id as usual. Yields nothing when escalation
        is off/unreachable, so the caller falls back to surfacing the error."""
        if self._model is not None or not config.escalation_available():
            return
        log.info("escalating failed turn to %s", config.ESCALATE_MODEL)
        yield ToolCall("escalation")
        yield Delta(f"\n_(that didn't go through — retrying with {config.ESCALATE_MODEL})_\n\n")
        sub = ClaudeEngine(system_extra=self._extra, model=config.ESCALATE_MODEL,
                           effort=config.ESCALATE_EFFORT, partial=self._partial,
                           max_turns=self._max_turns)
        try:
            async for ev in sub.run(user_text):
                # Adopt the sub's session BEFORE yielding Done: the caller
                # returns on Done, which abandons this generator at that yield
                # (same lesson as _maybe_rescue's history append). Without the
                # adoption, the reused engine would resume its stale pre-error
                # session next turn and the escalated exchange would silently
                # vanish from the model's memory.
                if isinstance(ev, Done) and ev.session_id:
                    self.session_id = ev.session_id
                yield ev
        finally:
            await sub.aclose()

    async def aclose(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None


# --- factory -----------------------------------------------------------------

def make_engine(*, system_extra: str = "", resume_messages: list[dict] | None = None,
                resume_session: str | None = None, unattended: bool = False,
                max_turns: int | None = None, partial: bool = True,
                model: str | None = None, effort=config.UNSET):
    """Build the engine for the configured backend, sharing one system prompt.

    `model`/`effort` override the configured defaults on the Claude backend
    (used by jobs that deserve a stronger brain, e.g. memory consolidation);
    the local backend ignores them — it always runs OLLAMA_MODEL."""
    from .agent import system_prompt

    extra = system_extra
    if unattended:
        extra += "\nYou are running unattended as a scheduled job — do not ask questions."

    if config.BACKEND == "claude":
        return ClaudeEngine(system_extra=extra, resume_session=resume_session,
                            max_turns=max_turns, partial=partial,
                            model=model, effort=effort)
    return OllamaEngine(system=system_prompt(extra), resume_messages=resume_messages,
                        mac=sys.platform == "darwin")
