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
| source    | `confluence`                | Confluence REST API (space)              |
| source    | `github` / `gitlab`         | repository trees (blob sha fingerprint)  |
| source    | `s3`                        | S3-compatible buckets (SigV4, no boto3)  |
| extract   | `auto`                      | markdown / html / text / pdf / docx/xlsx |
| chunk     | `auto` / `sections` / `size`| token, by-heading, fixed-size            |
| embed     | `default` / `openai`        | deterministic local / OpenAI-compatible  |
| index     | `memory` / `qdrant` / `pgvector` |                                      |

## Community plugins

_None published yet._ Add a row here when you publish a plugin; every row
includes: package (PyPI), available types, maintainer, status.

| package | types | maintainer | status |
| ------- | ----- | ---------- | ------ |