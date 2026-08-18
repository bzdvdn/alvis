"""Pipeline engine — orchestrates source → artifact → extract → chunk → embed → index."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from winnow.core.models import PipelineConfig


@dataclass
class PipelineResult:
    """Summary of a completed pipeline run."""

    documents_ingested: int
    chunks_indexed: int


class PipelineEngine:
    """Executes a pipeline defined declaratively in YAML.

    v0.1 placeholder — stages are wired in ROADMAP phases.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    @classmethod
    def from_yaml(cls, path: str) -> PipelineEngine:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls(PipelineConfig(**raw["pipeline"]))

    def run(self) -> PipelineResult:
        # TODO(v0.1): wire source → extract → chunk → embed → index stages.
        return PipelineResult(documents_ingested=0, chunks_indexed=0)