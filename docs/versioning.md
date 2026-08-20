# Versioning & deprecation policy

Alvis follows [semantic versioning](https://semver.org) on the **package
version** (see `pyproject.toml`), and a separate, scheme-specific contract
versioning for YAML / Canonical Content Tree (see
[docs/schema.md](schema.md)). This page is the package-level policy.

## Release numbers

- **`1.0.0rc1` (current release candidate).** The stable surface is frozen;
  RC releases may still contain release-blocking fixes, but no intentional
  contract changes. The stable surface below is honored from its freeze and
  beyond `1.0.0`.
- **`1.0.0`.** Freezes the stable surface; `MAJOR` thereafter means a
  breaking change to it. Anything not listed is public-but-internal and may
  change with a `MINOR` bump and a changelog note.

## Stable surface

Frozen at `1.0.0`. No *breaking* change ships for these names without a
`MINOR`→`MAJOR` bump and a release note:

| module                                                                  | promises                                                      |
| ----------------------------------------------------------------------- | ------------------------------------------------------------- |
| `alvis.plugin` / `alvis.plugin.Plugin`, entry-point group             | plugin SDK: `Plugin` + per-kind factory contract, discovery   |
| `alvis.config` (load, models)                                          | YAML schema v1 payloads load unchanged (RFC-first schema v2)  |
| indexed payloads (`__`-prefixed system fields, `__schema`)            | storage keys are versioned by `__schema`; re-index on bump    |
| `alvis.core.models` (CCT)                                              | `Document`/`Chunk`/`Artifact`/`SearchHit` construction         |
| `alvis.pipeline.PipelineEngine`, `.PipelineResult`, `alvis.run_*`     | programmatic run + result/delta fields                        |
| `alvis.factories.build_*`, `alvis.sources` / `alvis.chunk` / `alvis.embed` / `alvis.index` bases | adapter construction + adapter protocol signatures            |

Breaking additions still happen naturally inside *new* type strings and new
optional config keys (still governed by [docs/schema.md](schema.md) rules).

## Deprecation window

A stable-surface name is removed only after a warning cycle:

1. **Mark.** Add `@deprecated("replacement", remove_in=...)` from
   `alvis.deprecated`; the name keeps working and the warning names the
   replacement and the removal version.
2. **Wait one MAJOR.** `remove_in` must be at least the next major. For
   `0.x`, one `MINOR` is the minimum window (pre-1.0 rule above).
3. **Remove** in the release named by `remove_in`, with a changelog line.

Deprecations are loud and mechanical — `alvis.deprecated` annotates the
callable, `removed_in()` exposes the deadline for audits, and call sites get
`DeprecationWarning` (visible under `-W default::DeprecationWarning` or in
CI when running with `PYTHONWARNINGS=default`).

## Releasing

- Tag `vX.Y.Z`; CI builds/publishes the `bzdvdn/alvis` image (`X.Y.Z` +
  `latest`) and the container entrypoint reads the version via
  `alvis --version`.
- The Docker publish needs `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` secrets
  on the repository; building for the tag already works without them.
