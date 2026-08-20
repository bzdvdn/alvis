from __future__ import annotations

import json

import httpx
import pytest

from alvis.core.models import Chunk
from alvis.embed.openai import ApiEmbedder
from alvis.sources.base import SourceError


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(text=t, source_uri="s3://alvis/x.md") for t in texts]


async def test_embed_single() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        payload = json.loads(request.read().decode())
        assert payload["model"] == "text-embedding-3-small"
        assert payload["input"] == ["hello"]
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
                "model": "text-embedding-3-small",
            },
        )

    embedder = ApiEmbedder(
        base_url="http://embeddings.local/v1",
        model="text-embedding-3-small",
        transport=httpx.MockTransport(handler),
    )
    assert await embedder.embed(_chunks("hello")[0]) == [0.1, 0.2, 0.3]


async def test_embed_batch_preserves_order() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2]},
                    {"index": 0, "embedding": [0.1]},
                ]
            },
        )

    embedder = ApiEmbedder(
        base_url="http://embeddings.local/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    assert (await embedder.embed_batch(_chunks("a", "b"))) == [[0.1], [0.2]]


async def test_embed_batch_splits_by_batch_size() -> None:
    calls: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        data = json.loads(payload)
        calls.append(list(data["input"]))
        entries = [
            {"index": i, "embedding": [float(i)]} for i in range(len(data["input"]))
        ]
        return httpx.Response(200, json={"data": entries})

    embedder = ApiEmbedder(
        base_url="http://embeddings.local/v1",
        model="m",
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    vectors = await embedder.embed_batch(_chunks("a", "b", "c", "d", "e"))
    assert calls == [["a", "b"], ["c", "d"], ["e"]]
    assert vectors == [[0.0], [1.0], [0.0], [1.0], [0.0]]


async def test_embed_retries_on_5xx() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    embedder = ApiEmbedder(
        base_url="http://embeddings.local/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    await embedder.embed_batch(_chunks("x"))
    assert calls["n"] == 2


async def test_embed_no_token_env_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    embedder = ApiEmbedder(
        base_url="http://embeddings.local/v1",
        model="m",
        api_token_env="MISSING_VAR_982374",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SourceError, match="MISSING_VAR_982374"):
        await embedder.embed_batch(_chunks("x"))