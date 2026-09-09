# Retrieval evaluation

Ingestion has golden-snapshot tests (see [Test harness](../src/alvis/testing.py)).
`alvis.evaluation` is the retrieval-side equivalent: it proves that a
pipeline's chunking/embedding choices actually surface the right content,
without needing an LLM or API key.

## Cases

A case is a query paired with the document it should retrieve:

```yaml
# eval-cases.yaml
- query: "how do I install alvis with pip"
  expected_source_uri: "alvis-guide.md"
- query: "which vector index backends does alvis support"
  expected_source_uri: "alvis-guide.md"
```

`expected_source_uri` is matched as a substring against `SearchHit.source_uri`
— a filename is usually enough, no need for the full URI.

A case can also carry its own `principals` to test ACL enforcement itself —
overrides the harness-level `--principal` for just that case:

```yaml
- query: "internal design doc"
  expected_source_uri: "design.md"
  principals: ["eng"]      # as an eng user, this query should hit design.md
- query: "internal design doc"
  expected_source_uri: "design.md"
  principals: ["sales"]    # as a sales user, it should NOT (see report.misses)
```

## Running

Against an already-ingested index (same two-step flow as `alvis query`):

```bash
alvis run examples/eval-pipeline.yaml
alvis eval examples/eval-pipeline.yaml examples/eval-cases.yaml
```

```text
4 case(s): hit_rate=1.00 mrr=1.00
  [rank 1] 'how do I install alvis with pip' -> alvis-guide.md
  [rank 1] 'which vector index backends does alvis support' -> alvis-guide.md
  ...
```

`--min-hit-rate 0.9` exits non-zero when the rate drops below the
threshold — a CI gate against retrieval regressions. `--json` emits a
machine-readable report (`hit_rate`, `mrr`, `cases`, `misses`). Add
`--hybrid` to score the fused dense+BM25 ranking instead of dense-only —
run the same cases with and without it to see whether hybrid search is
worth it for your corpus:

```bash
alvis eval examples/eval-pipeline.yaml examples/eval-cases.yaml --hybrid
```

Add `--rerank` (`--rerank-base-url/--rerank-model/--rerank-api-token-env`) to
score the LLM-reranked ranking instead — the harness stays LLM-free by
default; `--rerank` is the one opt-in that needs an API key:

```bash
alvis eval examples/eval-pipeline.yaml examples/eval-cases.yaml --rerank
```

`--principal name` (repeatable) sets the default identity for every case
that doesn't declare its own `principals`.

## Metrics

- **hit rate** — fraction of cases whose expected document appeared
  anywhere in the top `--top-k` results.
- **MRR** (mean reciprocal rank) — `1/rank` averaged over cases (0 for a
  miss); rewards the expected document coming back *first*, not just
  somewhere in the list.

## Programmatically

```python
from alvis import evaluate, evaluate_async, load_cases, EvalCase

cases = load_cases("eval-cases.yaml")
report = evaluate("pipeline.yaml", cases, top_k=5)
report.hit_rate      # 0.0 - 1.0
report.mrr
report.misses        # EvalCaseResult entries that didn't hit

evaluate("pipeline.yaml", cases, principals=["eng"])  # default identity for every case
```

## Scope

This measures *retrieval* quality — did the right chunk come back — not
*answer* quality (faithfulness/relevancy of a synthesized response, which
needs an LLM judge). That is a separate harness for later; this one runs
in CI with no API key by default, same as the rest of the test suite
(`--rerank` is the exception, since reranking itself needs an LLM).
