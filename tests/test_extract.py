from __future__ import annotations

from alvis.core.models import Artifact, Document
from alvis.extract import AutoExtractor

MD = """\
# Project Docs

Intro paragraph.

## Setup

Run `pip install alvis`.

## Deployment

Deploy to production.
"""


async def test_extract_markdown_sections() -> None:
    artifact = Artifact(
        step_id="1",
        uri="https://x/docs",
        content_type="text/markdown",
        data=MD.encode(),
    )
    doc: Document = await AutoExtractor().extract(artifact)
    assert doc.title == "Project Docs"
    assert [s.heading for s in doc.sections] == [
        "Project Docs",
        "Setup",
        "Deployment",
    ]
    assert "Intro paragraph" in doc.sections[0].body


async def test_extract_html_sections() -> None:
    html = (
        "<html><h1>Guide</h1><p>Hello</p><h2>Step 1</h2>"
        "<p>Do the thing</p></html>"
    )
    artifact = Artifact(
        step_id="2",
        uri="https://x/page",
        content_type="text/html",
        data=html.encode(),
    )
    doc = await AutoExtractor().extract(artifact)
    assert doc.title == "Guide"
    assert any("Step 1" in s.heading for s in doc.sections)
    assert any("Do the thing" in s.body for s in doc.sections)