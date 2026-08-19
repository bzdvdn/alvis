"""Structured CSV extraction — row sections with header-aware bodies.

Header rule: the first row is treated as a header when every cell is non-empty
text; pure-numeric rows and single-column files are data (joined with ``|``).
"""

from __future__ import annotations

from winnow.core.models import Artifact
from winnow.extract import AutoExtractor, CsvExtractor


def _artifact(data: str, content_type: str = "text/csv") -> Artifact:
    return Artifact(
        step_id="s",
        uri="s3://b/data.csv",
        content_type=content_type,
        data=data.encode(),
        metadata={"title": "data.csv"},
    )


async def test_csv_extractor_joined_rows_without_header() -> None:
    artifact = _artifact("1,2\n3,4\n")
    document = await CsvExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == ["Row 1", "Row 2"]
    assert document.sections[0].body == "1 | 2"
    assert document.sections[1].body == "3 | 4"


async def test_csv_extractor_header_row_pairs_fields() -> None:
    artifact = _artifact("name,port\napi,8080\n")
    document = await CsvExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == ["Row 2"]
    assert document.sections[0].body == "name: api | port: 8080"


async def test_csv_extractor_skips_blank_rows() -> None:
    artifact = _artifact("1,2\n\n3,4\n")
    document = await CsvExtractor().extract(artifact)
    assert [s.heading for s in document.sections] == ["Row 1", "Row 2"]


async def test_csv_extractor_empty_file_has_no_sections() -> None:
    document = await CsvExtractor().extract(_artifact(""))
    assert document.sections == ()


async def test_csv_extractor_numeric_first_row_is_not_header() -> None:
    document = await CsvExtractor().extract(_artifact("8080,443\n"))
    assert document.sections[0].body == "8080 | 443"


async def test_auto_extractor_dispatches_csv() -> None:
    artifact = _artifact("name,team\nAda,core\n")
    document = await AutoExtractor().extract(artifact)
    assert document.sections[0].body == "name: Ada | team: core"


async def test_content_type_maps_csv() -> None:
    from winnow.sources.content_types import content_type

    assert content_type("a.csv") == "text/csv"