"""Structured JSON extraction — dotted-path sections."""

from __future__ import annotations

import pytest

from alvis.core.models import Artifact
from alvis.extract import AutoExtractor, JsonExtractor


def _artifact(data: bytes, content_type: str = "application/json") -> Artifact:
    return Artifact(
        step_id="s",
        uri="s3://b/data.json",
        content_type=content_type,
        data=data,
        metadata={"title": "data.json"},
    )


async def test_json_extractor_flattens_nested_object() -> None:
    import json

    payload = {
        "user": {"name": "Ada", "address": {"city": "London"}},
        "active": True,
    }
    artifact = _artifact(json.dumps(payload).encode())
    document = await JsonExtractor().extract(artifact)
    assert document.title == "data.json"
    assert [s.heading for s in document.sections] == [
        "user.name",
        "user.address.city",
        "active",
    ]
    assert document.sections[0].body == "Ada"
    assert document.sections[2].body == "True"


async def test_json_extractor_indexes_arrays() -> None:
    import json

    artifact = _artifact(json.dumps({"items": [7, 8, {"nested": "x"}]}).encode())
    document = await JsonExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == [
        "items.0",
        "items.1",
        "items.2.nested",
    ]
    assert document.sections[2].body == "x"


async def test_json_extractor_scalar_root() -> None:
    artifact = _artifact(b"42")
    document = await JsonExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == [""]
    assert document.sections[0].body == "42"


async def test_json_extractor_skips_null_values() -> None:
    import json

    artifact = _artifact(json.dumps({"a": None, "b": "kept"}).encode())
    document = await JsonExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == ["b"]


async def test_json_extractor_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="invalid JSON"):
        await JsonExtractor().extract(_artifact(b"{not json"))


async def test_auto_extractor_dispatches_json() -> None:
    import json

    artifact = _artifact(json.dumps({"version": 3}).encode())
    document = await AutoExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == ["version"]
    assert document.sections[0].body == "3"


async def test_content_type_maps_json() -> None:
    from alvis.sources.content_types import content_type

    assert content_type("a.json") == "application/json"