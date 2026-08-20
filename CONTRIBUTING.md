# Contributing to Alvis

Thanks for contributing. Alvis is a no-code knowledge ingestion engine, and
most of its value is in breadth: the more sources, formats, and stores it
speaks, the more useful it is. This guide is the cover page for **how to write
a connector** — the single most common contribution.

Read [CONSTITUTION.md](CONSTITUTION.md) first: the roadmap and cross-cutting
rules live there and in [ROADMAP.md](ROADMAP.md).

---

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests
.venv/bin/mypy src/alvis
.venv/bin/python -m pytest
```

- Python ≥ 3.10, ruff (lint), mypy `--strict`, pytest (asyncio auto mode).
- A feature ships only with its documentation (cross-cutting rule) and its
  tests — see "Connector fixture + golden test" below, which is enforced in CI.
- YAML contract and Canonical Content Tree changes are RFC-first: open a
  proposal before touching `config/models.py` or `core/models.py`.

---

## How to write a connector

A connector turns a source of documents into Alvis `Artifact`s so the rest of
the pipeline (extract → chunk → embed → index) can process them. Alvis never
reads the raw bytes of a source itself — it always goes through a connector's
`fetch()`.

### 1. The contract

`src/alvis/sources/base.py` defines the interface:

```python
class Source(Protocol):
    async def fetch(self) -> list[Artifact]: ...
```

`Artifact` (`alvis.core.models`) carries exactly:

| field          | meaning                                                            |
| -------------- | ------------------------------------------------------------------ |
| `step_id`      | stable, unique id for this document (page id, blob sha, ...)        |
| `uri`          | canonical URL/path the document lives at (used for retrieval links) |
| `content_type` | MIME type Alvis's extract stage understands (see step 3)           |
| `data`         | the raw bytes to be ingested                                        |
| `metadata`     | extra fields surfaced to users; keep it small and serialisable      |

`fetch()` either returns artifacts or raises `SourceError`
(`alvis.sources.base`) with an optional `status_code`.

**Optional: cheap listing (recommended).** If your source can fingerprint a
document without downloading its body — an S3 ETag, a GitLab/GitHub blob sha, a
Confluence page version — also implement `ListingSource`
(`alvis.sources.base`):

```python
class ListingSource(Source, Protocol):
    async def list_documents(self) -> list[DocumentMeta]: ...
    async def fetch(self, *, uris: set[str] | None = None) -> list[Artifact]: ...
```

- `list_documents()` returns a `DocumentMeta` (`uri`, `step_id`, `content_type`,
  `fingerprint`) per document, doing **only** the cheap scan call(s).
- `fetch(uris=...)` must download **only** the requested URIs — `uris=None`
  means "everything". The engine's `--incremental` mode lists first, skips
  documents whose stored fingerprint already matches, and calls
  `fetch(uris=...)` with just the wanted URIs, so unchanged remote objects are
  never fetched. A connector that skips this interface falls back to
  content-hash fingerprinting (still skips processing, not downloads).
- `gitlab.py` / `github.py` / `s3.py` / `confluence.py` are complete
  `ListingSource` examples.

### 2. Layout and naming

Look at how the built-ins are organised — each adapter is one small module:

- `src/alvis/sources/confluence.py` — a REST API adapter, the best template
  for an HTTP connector.
- `src/alvis/sources/fs.py` — the local-filesystem adapter.
- `src/alvis/sources/github.py`, `gitlab.py`, `s3.py` — REST adapters that
  accept a `transport=` kwarg for testing.

For an HTTP connector that talks to a REST API, use `HttpClient`
(`alvis.sources.http`): it gives you retries/backoff on 429/5xx/network
errors, `Retry-After` honoring, token/basic auth from env vars
(`api_token_env`, `username`), optional `verify=False` for self-signed TLS, and
a `max_bytes` cap so oversized responses are aborted mid-read instead of
buffered. Confluence is a 70-line example of a full adapter.

Reach for `transport=` kwargs / `header_hook=` when the service needs special
signing (see `s3.py` for a SigV4 example).

### 3. Wire it in — the five touch points

1. **Module** — `src/alvis/sources/<name>.py` implementing `fetch()`.
2. **Export** — add it to `__all__` in `src/alvis/sources/__init__.py`.
3. **Registry** — `src/alvis/registry.py`: add the type string to
   `KNOWN_SOURCES` so `alvis validate` recognises it.
4. **Factory** — `src/alvis/factories.py`:
   - `build_source(...)` — map `config.type == "<name>"` to your class.
   - `source_identity(config)` — a stable `"<name>:<key>@<host>"` string; the
     index uses it to namespace points so `reconcile` prunes only your source's
     stale documents. Pick discriminator keys that uniquely identify a data
     scope (e.g. GitLab: `project@host`).
5. **DSL** — `src/alvis/dsl.py`: add a `dsl.<name>(...)` builder mirroring the
   YAML config keys, so the Python DSL and the YAML schema stay in parity.

Config validation lives in `src/alvis/config/models.py` (`SourceConfig` is
permissive `dict` today; type-specific keys are validated in the adapter).
Document the YAML keys in the README source table.

### 4. Content types and formats

`alvis.sources.content_types` maps extensions to MIME types. Your connector
should produce content types the `auto` extractor understands — Markdown
(`text/markdown`), HTML (`text/html`), plain text / code (`text/plain`), or
PDF/DOCX/XLSX for the `alvis[documents]` extra. If a remote format needs an
extension (e.g. GitLab raw blobs), derive it from the object name and pass it
through `content_type()`.

### 5. Every connector ships two things — enforced in CI

No connector lands without:

1. **A fixture** — a `tests/fixtures/` corpus of realistic sample data the
   adapter ingests (a couple of small files is plenty). Fixtures must not live
   under a directory Alvis itself would ingest in tests.
2. **A golden test** — a test marked `@pytest.mark.golden` that runs the
   connector against canned service responses and diffs the `Artifact` output
   against a committed snapshot.

`src/alvis/testing.py` provides the harness (used by every built-in in
`tests/test_golden_sources.py`):

```python
from alvis.testing import MockServer, artifacts_snapshot, assert_golden

@pytest.mark.golden
async def test_golden_my_source() -> None:
    server = MockServer()
    server.on("GET", "/api/v1/items", json_payload={"items": [{"id": "a", "body": "# Hi"}]})

    source = MySource(url="http://example.com", transport=server.transport)
    artifacts = await source.fetch()

    assert_golden("my_source", artifacts_snapshot(artifacts))
```

- `MockServer` is an `httpx.MockTransport` under the hood — no network. Point
  your `HttpClient` at `server.transport` (via a `transport=` kwarg, or
  `source.client.transport = server.transport` when the constructor builds its
  own client, as Confluence does). **Every request must have a route** —
  unimapped routes raise, so the fixture doubles as a contract check on which
  endpoints your connector calls. `server.request_log()` lets you assert on
  the calls themselves.
- `artifacts_snapshot(...)` renders the `fetch()` result as a stable,
  serialisable list so the snapshot is diff-able in review. Pass `root=` to
  scrub machine-specific absolute paths (see the `fs` golden test).
- Snapshots live in `tests/golden/<name>.golden.json`. Run
  `ALVIS_ACCEPT=1 pytest -m golden` to (re)write them, review the diff, and
  commit. CI runs `pytest -m golden` **read-only** (no `ALVIS_ACCEPT`), so
  drift and missing snapshots fail loudly.

**Register the golden test in `tests/golden/manifest.json`** under the
connector's type string pointing at the test file. The CI script
`scripts/check_golden_coverage.py` fails if a connector has a manifest entry
but no test/snapshot — keep the three in a single commit.

### 6. Beyond the connector

If you add a new parser, chunker, embedder, or indexer, follow the same shape:
module → export → `KNOWN_*` in `registry.py` → `build_*` in `factories.py` →
`dsl.*` builder → docs. Indexers that hold state (e.g. a new vector store)
must implement idempotent upsert + a `reconcile` pass for the per-source prune
to work.

## Write a plugin, not a fork (v1.1)

You do **not** need to modify this repository to ship an adapter. A plugin is
an ordinary installed Python package that registers itself under the
`alvis.plugins` entry-point group and declares a `alvis.plugin.Plugin`:

```toml
# pyproject.toml
[project.entry-points."alvis.plugins"]
kb-catalog = "kb_plugin:plugin"
```

```python
# kb_plugin/__init__.py
from alvis.plugin import Plugin

def _catalog(*, config, max_bytes=None):   # factory contract (see alvis/plugin.py)
    ...

plugin = Plugin(
    name="kb-catalog",
    version="0.1.0",
    sources={"catalog": _catalog},         # also: extractors, chunkers, embedders, indexers
)
```

After `pip install .`, the adapter is accepted by `alvis validate`, listed by
`alvis plugins`, and resolved by the stage factories — zero core changes.
Plugins are validated their own adapter (mirroring built-ins), so plugin types
may read any `config:` keys. The reference implementation is
`examples/kb-plugin` (a `catalog` source with cheap-listing incremental
support); copy it as your template and keep the tutorial contract: hello-world
example + test + docs. External plugins are tracked in [docs/plugins.md]
(docs/plugins.md).

---

## Contribution process

1. Fork, create a branch (`add-magnolia-connector`).
2. Implement the connector + fixture + golden test + docs + README table row
   as one commit — the golden manifest, snapshot, and test never split.
3. Run the full gate: `ruff check src tests`, `mypy src/alvis`,
   `pytest` (which includes `-m golden` read-only), and
   `python scripts/check_golden_coverage.py`.
4. Open the pull request. CI runs lint, typecheck, the full suite on
   Python 3.10/3.11/3.12, and a dedicated `golden` job that re-runs snapshots
   read-only and enforces coverage.

## Cross-cutting rules (from ROADMAP)

- Every plugin lands only with: hello-world example + test fixture + CI golden
  test.
- Any change to the YAML contract or Canonical Content Tree is an RFC-first
  change.
- No feature ships without the chapter it documents.
- Breaking a stable-surface name (see [docs/versioning.md](docs/versioning.md))
  needs a semver `MAJOR` (or a `MINOR` before `1.0.0`), a `deprecated()`
  warning cycle, and a changelog line — never a silent break.