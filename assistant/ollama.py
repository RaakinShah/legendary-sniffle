"""Thin async client for a local Ollama server (the offline brain).

Talks to /api/chat with streaming + tool calling. Ollama returns NDJSON: one
JSON object per line, each carrying an incremental `message` and a final line
with `done: true`. Tool calls arrive as structured objects whose `arguments`
are already a dict (no JSON re-parsing needed).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from . import config


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        """One pooled client per OllamaClient, so every agent step reuses the
        keep-alive connection to localhost instead of a fresh TCP handshake."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = True,
        think: bool | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield response chunks from /api/chat. Each chunk is a parsed dict with
        a `message` ({role, content, tool_calls?, thinking?}) and a `done` flag."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {"num_ctx": config.OLLAMA_NUM_CTX},
        }
        if config.OLLAMA_KEEP_ALIVE:
            payload["keep_alive"] = config.OLLAMA_KEEP_ALIVE
        if tools:
            payload["tools"] = tools
        if think is not None:
            payload["think"] = think

        try:
            async with self._http().stream("POST", f"{self.host}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")
                    raise OllamaError(self._explain(resp.status_code, body))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError(str(chunk["error"]))
                    yield chunk
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Can't reach Ollama at {self.host} — is it running? ({exc})"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise OllamaError(
                f"{self.model} timed out. A 20B model is slow to load the first time; "
                f"try again, or use a smaller ASSISTANT_OLLAMA_MODEL. ({exc})"
            ) from exc

    def _explain(self, status: int, body: str) -> str:
        if status == 404 and "model" in body.lower():
            return f"Model {self.model} isn't installed. Run: ollama pull {self.model}"
        return f"Ollama HTTP {status}: {body[:200]}"

    async def warm(self) -> None:
        """Best-effort: load the model into memory so the first real turn is fast."""
        try:
            async for _ in self.chat([{"role": "user", "content": "hi"}], stream=False):
                break
        except Exception:
            pass
