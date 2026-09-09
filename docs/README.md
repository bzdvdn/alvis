# Alvis — Documentation

Guides for the no-code knowledge ingestion engine.

- [Getting started](getting-started.md) — install, first pipeline, first index
- [FAQ](faq.md) — short answers to common questions
- [Extending Alvis](../CONTRIBUTING.md) — the "how to write a connector" guide
- [Plugin registry](plugins.md) — built-in and community plugins (v1.1 SDK)
- [Observability](observability.md) — structured logging, metrics, tracing, Prometheus export
- [Python DSL guide](dsl.md) — describing pipelines from code
- [Retrieval evaluation](evaluation.md) — hit rate / MRR harness, `alvis eval`
- [Schema & versioning](schema.md) — the YAML contract and Canonical Content Tree evolution policy
- [Release 1.0 tracker](release-1.0.md) — open issues blocking the first stable release

## Reference

- [Pipeline contract](schema.md) — `pipeline:` grammar, `schema_version`
- [Connector contract](../src/alvis/sources/base.py) — `Source.fetch()` → `Artifact`
- [Test harness](../src/alvis/testing.py) — `MockServer`, `assert_golden` (connector fixture)

## More

- [CONSTITUTION.md](../CONSTITUTION.md) — purpose, scope, open-source strategy
- [ROADMAP.md](../ROADMAP.md) — build plan