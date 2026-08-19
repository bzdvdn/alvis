# Schema & Canonical Content Tree — versioning policy

Two public contracts are versioned, and both freeze at **v1**:

- **YAML pipeline schema** (`pipeline.schema_version`, default `1`).
- **Canonical Content Tree** (`Document.schema_version`, constant
  `winnow.core.models.CCT_SCHEMA_VERSION`, currently `1`).

Both live in `docs/`. They are RFC-first contracts: any change to the YAML
schema or the Canonical Content Tree requires an RFC (see CONSTITUTION.md),
and only the core team may bump a major version.

## Version numbers

- **Major**: breaking change — a previously-valid config/tree is rejected or
  semantically changes meaning. Bumped by RFC; support for the old major is
  dropped only after the deprecation window.
- **Minor**: additive change — new optional fields, new stages/strategies,
  new conventions. Fully backward compatible.
- **Patch**: fixes that do not alter the contract's meaning.

## Backward-compatible evolution rules (within a major)

1. **Additive only.** New fields must be *optional* with a default that keeps
   old payloads meaning the same thing. Example: `ChunkConfig` gained
   `max_chars`/`overlap_chars` in the `size` strategy without changing `auto`.
2. **Defaults hold meaning.** Omitting a field must behave identically to the
   version that shipped it. Old YAML files and old serialized trees must load
   unchanged (their `schema_version` stays `1`).
3. **Never repurpose.** Do not silently change the semantics of an existing
   field; introduce a new field instead and deprecate the old one.
4. **Validate loudly.** Unknown versions and unknown extra fields fail fast
   (`ConfigError`) rather than being ignored, so a future reader never misreads
   a document as the current version.
5. **Deprecation window.** A field may be deprecated in one release and
   removed only in the next major. Deprecations are warned about, not silent.

## How stays backward compatible in practice

- `PipelineConfig(schema_version=1)` and `Document(schema_version=1)` are
  *defaults*: historical artifacts produced before the field was added
  construct with version `1` and need no migration.
- New extract setting `max_bytes` (memory-safe handling of large
  PDF/DOCX/XLSX) defaults to "no cap", preserving previous behaviour.
- New optional `ChunkConfig` keys validate as integers but change nothing for
  existing strategies.
- Runtime models are pydantic frozen models: serialization round-trips and
  validates against the same contract, so stored trees can be version-checked
  at read time.

## Breaking change process

1. RFC describes the change, affected payloads, and a migration path.
2. Bump the affected major (or plan `schema_version: 2`).
3. Release old version with deprecation warnings for one cycle.
4. New major rejects `schema_version: 1` with a migration hint.