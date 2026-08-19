# Plugin registry

Winnow adapters are pluggable (v1.1 Plugin SDK). Plugins are installed Python
packages that register under the `winnow.plugins` entry-point group; `winnow
plugins` lists what discovery found, and `winnow validate`/`run` accept their
type strings without core changes. See [CONTRIBUTING](../CONTRIBUTING.md) for
the plugin contract and [examples/kb-plugin](../examples/kb-plugin) for a
reference implementation.

## Built-in adapters (shipped with Winnow)

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