"""Drive Apple's Foundation Models through the native Swift bridge (bridge/aide-fm).

The Python apple-fm-sdk only exposes the on-device model; reaching Private Cloud
Compute (the far stronger AFM 3 Cloud model) needs the native FoundationModels
framework, which lives behind a small compiled Swift helper. This module spawns
that helper, speaks its line-delimited JSON protocol, and presents the same
Delta/Text/ToolCall/Done stream every other engine does, so the GUI/CLI/jobs
don't care that a subprocess is doing the inference.

Today the helper runs the on-device model (so ASSISTANT_APPLE_CLOUD just exercises
this path); when it is rebuilt against the macOS 27 SDK, the same protocol carries
PCC with no Python change. The helper is selected over the in-process
FoundationModelsEngine only when the user opts into cloud AND the binary exists.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from . import config
from .engine import Delta, Done, Text, ToolCall, _APPLE_CORE_TOOLS
from .log import get_logger

log = get_logger(__name__)


def binary_path() -> Path:
    """The compiled Swift helper, alongside its source under bridge/."""
    return Path(__file__).resolve().parent.parent / "bridge" / "aide-fm"


def available() -> bool:
    return binary_path().exists()


class AppleBridgeEngine:
    """Agent loop over the native FoundationModels bridge subprocess."""

    def __init__(self, system: str, *, cloud: bool, mac: bool = True) -> None:
        from . import toolkit
        self._system = system
        self._cloud = cloud
        specs, self.dispatch = toolkit.build_toolset(mac=mac)
        # Same curated core the in-process engine uses, for the same reasons
        # (small-model focus + the on-device preamble budget). PCC could take the
        # full set, but the helper reports the on-device model until the macOS 27
        # SDK is installed, so stay curated for now.
        self.specs = [s for s in specs if s["function"]["name"] in _APPLE_CORE_TOOLS]
        self._proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None

    async def _ready(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            str(binary_path()), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        tools = [{"name": s["function"]["name"],
                  "description": s["function"].get("description", ""),
                  "parameters": s["function"].get("parameters", {})}
                 for s in self.specs]
        await self._send({"op": "init", "system": self._system,
                          "cloud": self._cloud, "tools": tools})
        msg = await self._recv()
        if not msg or msg.get("type") != "ready":
            raise RuntimeError(f"bridge failed to start: {msg}")

    async def warm(self) -> None:
        await self._ready()

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self._proc.stdin.drain()

    async def _recv(self) -> dict | None:
        assert self._proc and self._proc.stdout
        line = await self._proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    async def run(self, user_text: str):
        from .util import redact
        try:
            await self._ready()
        except Exception as exc:  # noqa: BLE001
            yield Text(f"⚠️ {redact(str(exc))}")
            yield Done("error")
            return

        await self._send({"op": "turn", "message": user_text})
        while True:
            msg = await self._recv()
            if msg is None:                       # bridge died mid-turn
                yield Text("⚠️ the Apple bridge stopped unexpectedly")
                yield Done("error")
                return
            kind = msg.get("type")
            if kind == "delta":
                yield Delta(msg.get("text", ""))
            elif kind == "tool_call":
                yield ToolCall(msg.get("name", ""))
                result = await self._dispatch(msg.get("name", ""), msg.get("args", "{}"))
                await self._send({"op": "tool_result", "id": msg.get("id"), "result": result})
            elif kind == "final":
                text = msg.get("text", "")
                if text.strip():
                    yield Text(text)
            elif kind == "done":
                yield Done("success")
                return
            elif kind == "error":
                yield Text(f"⚠️ {redact(str(msg.get('message', 'bridge error')))}")
                yield Done("error")
                return

    async def _dispatch(self, name: str, args_json: str) -> str:
        try:
            args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})
        except json.JSONDecodeError:
            args = {}
        handler = self.dispatch.get(name)
        if handler is None:
            return f"Error: unknown tool '{name}'."
        try:
            return await handler(args or {})
        except Exception as exc:  # noqa: BLE001 - report to the model, don't crash the turn
            return f"Tool '{name}' raised: {exc}"

    async def aclose(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.returncode is None:
                await self._send({"op": "shutdown"})
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    self._proc.kill()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
