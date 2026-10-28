# Plugin registry

Alvis adapters are pluggable (v1.1 Plugin SDK). Plugins are installed Python
packages that register under the `alvis.plugins` entry-point group; `alvis
plugins` lists what discovery found, and `alvis validate`/`run` accept their
type strings without core changes. See [CONTRIBUTING](../CONTRIBUTING.md) for
the plugin contract and [examples/kb-plugin](../examples/kb-plugin) for a
reference implementation.

## Local companion plugins (`--plugins <dir>`)

Not every adapter deserves a package. A directory of plain `.py` files can
register adapters without packaging or installing: pass it explicitly with
`--plugins` and each file is loaded as an independent module. A file either
assigns a module-level `plugin: Plugin` or calls `install_plugin()` itself:

```python
# plugins/svc_demo.py  — one file, one plugin
from alvis.plugin import Plugin

def _svc(*, config, max_bytes=None) -> object:
    ...

plugin = Plugin(
    name="svc-demo",
    version="0.0.1",
    sources={"svc_demo": _svc},
)
```

```bash
alvis validate catalog.yaml --plugins ./plugins
alvis run --plugins ./plugins
alvis plugins --plugins ./plugins          # lists it too
```

Conventions and guarantees:

- Underscore-prefixed files (`_helpers.py`) are ignored.
- Every file must be self-contained; a file that fails to import is skipped
  with a warning (same fail-open policy as entry points).
- Each `(directory, file)` pair is loaded once per process.
- This executes your local code, so it is opt-in by flag and never implicit.

## Built-in adapters (shipped with Alvis)

| kind      | type                        | notes                                    |
| --------- | --------------------------- | ---------------------------------------- |
| source    | `fs`                        | local filesystem                         |
| source    | `confluence`                | Confluence REST API (space or page tree) |
| source    | `github` / `gitlab`         | repository trees (blob sha fingerprint)  |
| source    | `s3`                        | S3-compatible buckets (SigV4, no boto3)  |
| extract   | `auto`                      | markdown / html / text / pdf / docx/xlsx |
| chunk     | `auto` / `sections` / `size`| token, by-heading, fixed-size            |
| embed     | `default` / `openai`        | deterministic local / OpenAI-compatible  |
| index     | `memory` / `qdrant` / `pgvector` |                                      |

## Simpler source plugins: document providers

If your plugin only knows how to list documents (id, title, content, a few
provenance fields), do not reimplement `Source`/`ListingSource` glue. Implement
`DocumentProvider.documents()` and wrap it in `ProviderSource`:

```python
from alvis.sources import ProviderSource, SourceDocument

class CatalogProvider:
    name = "catalog"

    async def documents(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                id="catalog://widget",
                uri="https://catalog.example.com/widget",
                title="Widget",
                content="# Widget\n\n...",
                content_type="text/markdown",
                metadata={"owner": "sales"},
            )
        ]

def _catalog(*, config, max_bytes=None):
    return ProviderSource(CatalogProvider(), name="catalog", max_bytes=max_bytes)

plugin = Plugin(
    name="catalog",
    version="0.1.0",
    sources={"catalog": _catalog},
    source_identities={"catalog": lambda config: str(config.config["path"])},
)
```

`ProviderSource` handles content fingerprints, `DocumentMeta`/`Artifact`
construction, `fetch(uris=...)` filtering, provider caching, duplicate-id
detection, and fail-fast `SourceError` wrapping. It stamps `documentId`,
`sourceUrl`, `title`, and `source` into chunk metadata (provider values win).
It fingerprints by content, so every run reads every body; if your service can
fingerprint cheaply (ETag, blob sha, page version), implement `ListingSource`
directly instead.

## Source identity (required for multi-instance plugins)

Reconcile state is namespaced by `source_identity(config)`. Built-in types
derive theirs from the config (`gitlab:group/x@host`, `confluence:page:123@...`).
For plugin sources the default is just the type string, so **two pipelines
using the same plugin type over different corpora would prune each other's
points**. Any plugin type whose config selects a corpus should declare
`source_identities` (see the example above). The core prefixes the returned
value with `"<type>:"`.

## Community plugins

_None published yet._ Add a row here when you publish a plugin; every row
includes: package (PyPI), available types, maintainer, status.

| package | types | maintainer | status |
| ------- | ----- | ---------- | ------ |