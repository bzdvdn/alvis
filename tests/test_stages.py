"""Stage decomposition — declared order, unique names, and applicability gating."""

from __future__ import annotations

from pathlib import Path

from winnow import dsl
from winnow.observability import Metrics, configure_observability, reset_observability
from winnow.pipeline.engine import PipelineEngine
from winnow.pipeline.stages import (
    STAGES,
    CommitStage,
    EmbedStage,
    ExtractStage,
    FetchStage,
    ReconcileStage,
    UpsertStage,
)


def test_stage_order_is_declared_and_names_unique() -> None:
    classes = [type(stage) for stage in STAGES]
    assert classes == [
        FetchStage,
        ExtractStage,
        EmbedStage,
        UpsertStage,
        ReconcileStage,
        CommitStage,
    ]
    names = [stage.name for stage in STAGES]
    assert len(set(names)) == len(names)
    assert {"fetch", "extract", "embed", "upsert", "reconcile", "commit"} == set(names)


def _stages(m: Metrics) -> set[str]:
    return {
        key.split("stage=")[1]
        for key in m.snapshot()["histograms"]["pipeline_stage_seconds"]
    }


async def test_run_without_index_skips_write_stages(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nhello content here\n", encoding="utf-8")
    m = Metrics()
    configure_observability(metrics=m)
    try:
        await PipelineEngine(dsl.pipeline(dsl.fs(path=str(corpus)))).run()
    finally:
        reset_observability()
    stages = _stages(m)
    assert {"fetch", "extract"} <= stages
    assert not ({"embed", "upsert", "reconcile", "commit"} & stages)


async def test_run_with_index_engages_all_write_stages(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\nhello content here\n", encoding="utf-8")
    m = Metrics()
    configure_observability(metrics=m)
    try:
        await PipelineEngine(
            dsl.pipeline(dsl.fs(path=str(corpus)), index=dsl.memory())
        ).run()
    finally:
        reset_observability()
    stages = _stages(m)
    assert {"fetch", "extract", "embed", "upsert", "reconcile"} <= stages
    assert "commit" not in stages  # commit only engages with a DocStore