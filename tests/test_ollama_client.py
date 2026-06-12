"""OllamaClient against httpx.MockTransport: NDJSON streaming, no server."""

import asyncio
import json

import httpx
import pytest


def _client_with(handler):
    """Build an OllamaClient whose pooled http client uses a MockTransport."""
    from assistant.ollama import OllamaClient

    client = OllamaClient(host="http://ollama.test", model="test-model")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _collect(client, messages=None):
    async def go():
        return [c async for c in client.chat(
            messages or [{"role": "user", "content": "hi"}])]
    return asyncio.run(go())


def test_chat_yields_parsed_chunks_in_order():
    chunks = [
        {"message": {"role": "assistant", "content": "Hel"}},
        {"message": {"role": "assistant", "content": "lo"}},
        {"message": {"role": "assistant", "content": ""}, "done": True},
    ]
    # Blank and non-JSON lines must be skipped, not crash the stream.
    body = (json.dumps(chunks[0]) + "\n\n" + "this is not json\n"
            + json.dumps(chunks[1]) + "\n" + json.dumps(chunks[2]) + "\n")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, content=body.encode())

    client = _client_with(handler)
    got = _collect(client)
    assert got == chunks                                    # parsed, in order
    assert seen["url"] == "http://ollama.test/api/chat"
    assert seen["payload"]["model"] == "test-model"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "hi"}]


def test_connect_failure_raises_ollama_error():
    from assistant.ollama import OllamaError

    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = _client_with(handler)
    with pytest.raises(OllamaError) as exc_info:
        _collect(client)
    msg = str(exc_info.value)
    assert "http://ollama.test" in msg                      # names the host
    assert "is it running" in msg                           # actionable hint


def test_missing_model_gets_install_hint():
    from assistant.ollama import OllamaError

    def handler(request):
        return httpx.Response(404, json={"error": "model 'test-model' not found"})

    client = _client_with(handler)
    with pytest.raises(OllamaError) as exc_info:
        _collect(client)
    assert "ollama pull test-model" in str(exc_info.value)


def test_error_chunk_raises_ollama_error():
    from assistant.ollama import OllamaError

    def handler(request):
        return httpx.Response(200, content=json.dumps(
            {"error": "out of memory"}).encode())

    client = _client_with(handler)
    with pytest.raises(OllamaError, match="out of memory"):
        _collect(client)


def test_warm_failure_is_swallowed():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = _client_with(handler)
    # warm() is documented best-effort: a dead server must not raise.
    assert asyncio.run(client.warm()) is None


def test_aclose_is_idempotent():
    from assistant.ollama import OllamaClient

    async def go():
        client = OllamaClient(host="http://ollama.test")
        await client.aclose()          # never opened: no-op
        client._http()                 # open the pooled client (no request sent)
        await client.aclose()
        assert client._client is None
        await client.aclose()          # double close: still a no-op

    asyncio.run(go())
