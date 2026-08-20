# Release 1.0 — Open Issues

Analysis tracker for the first stable release. Each entry below is written to
become a GitHub issue on `bzdvdn/alvis`; keep it in sync with the issue board
until 1.0.0 ships.

Repo: `bzdvdn/alvis` (git remote + PyPI project + Docker Hub namespace).
Target: `1.0.0` from `0.6.0`, classifier `5 - Production/Stable`. Product
renamed from *winnow* to *Alvis* — PyPI name `winnow` is taken by an unrelated
package; **Alvis** (the All-Wise dwarf) is the new identity.

---

## I-1 Package metadata contradicts "stable"

**Severity:** release blocker · **Effort:** S

`pyproject.toml` declares `Development Status :: 3 - Alpha`, README says "Status
v0.6.0", and version remains `0.6.0`. A stable release must say what it is.

**Acceptance:**
- [ ] version bumped to `1.0.0`
- [ ] classifier set to `5 - Production/Stable`
- [ ] README "Status" rewritten for 1.0 (stable contract language)
- [ ] CHANGELOG.md created covering 0.1 → 1.0 (see I-3)

## I-2 No release publishing pipeline

**Severity:** release blocker · **Effort:** M

CI has no PyPI workflow; only the Docker image is built/pushed, and the push is
gated on `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` secrets that are not configured
(`.github/workflows/ci.yml:90`). `git remote` is not configured either.

**Acceptance:**
- [ ] `git remote add origin git@github.com:bzdvdn/alvis.git` + pushed
- [ ] CI publishes `sdist`+`wheel` to TestPyPI on tag, validated, then PyPI
- [ ] Docker Hub push verified once end-to-end from a tag (secrets configured)
- [ ] release checklist documents the tag → publish flow

## I-3 No CHANGELOG

**Severity:** release blocker · **Effort:** S

No `CHANGELOG.md` exists; the audit trail lives only in commit messages.

**Acceptance:**
- [ ] `CHANGELOG.md` present, `0.1` → `1.0.0` per-version entries
- [ ] each entry notes breaking changes and migration notes

## I-4 Real-service integration tests absent from CI

**Severity:** release blocker · **Effort:** M

Qdrant/pgvector/Confluence paths are "live-verified" manually; the CI `docker`
job only *builds* the image. Golden+unit coverage is strong but no CI run
exercises a real index or the incremental path end-to-end.

**Acceptance:**
- [ ] CI job runs `fs → qdrant` (qdrant docker service) e2e: index → query → reconcile
- [ ] CI job re-runs incremental: second run ingests zero chunks
- [ ] CI job covers a second index backend (pgvector) or documents why not

## I-5 Qdrant index contract is Phase 1 only

**Severity:** must-decide before 1.0 · **Effort:** L (or document-only)

`document_id`-ledger reconcile and "mirror ownership" were deferred by design:
point identity and `reconcile` still key on `source_uri`
(`src/alvis/index/qdrant.py:55`), while stable `document_id` is already stored
in the payload (`__document_id`). Corporate stores want rename-stable identity.

**Acceptance (choose one):**
- [ ] A: land Phase 2 — reconcile/prune by `__document_id`, document_id-ledger
- [ ] B: document as a known limitation in the 1.0 release notes + README
      (renames re-ingest until a manual prune)

## I-6 Placeholder embedder is the runtime default

**Severity:** footgun · **Effort:** S

`default` emits deterministic fake vectors; as a *default* it makes the stable
distribution silently index garbage embeddings in corpora that skip config.

**Acceptance:**
- [ ] running `alvis run` with `embed.type: default` emits a loud warning
- [ ] (or) `default` must be explicitly selected in config
- [ ] release notes call out the placeholder role of `default`

---

## Follow-up debt (not blockers)

- **F-1** Retrieval breadth (roadmap v0.3, open) — **defer to v2.x, per-item,**
  and state each as an explicit non-goal in the 1.0 release notes:
  - *Multi-turn history* — an application/chat-layer concern, not core
    ingestion; the read-side (e.g. NikaRD) already owns it. Non-goal.
  - *Reranking (cross-encoder)* — external model dependency, violates the
    "zero-dep beside the core" principle. Non-goal.
  - *Hybrid search (dense + BM25/full-text)* — touches every index backend
    (Qdrant sparse / pgvector tsvector); highest cost of all. Non-goal.
  - *LLM evaluation harness* — heavy, needs an LLM judge. Retrieval-quality
    proof for 1.0 is the golden snapshots plus the real-service e2e with
    retrieval assertions (see I-4). Non-goal.
  - *Metadata filters* — NOT deferred blindly: it is the only item with a
    **contract-freeze argument** (`alvis.query(text, top_k)` and
    `Indexer.search` signatures frost at 1.0; adding filters later becomes a
    breaking change). Current architecture already scopes by collection
    (one pipeline → one collection), so query-side prefilters are **not**
    needed today. Decision: ship without filters, but keep the query/search
    surface open — optional keyword-only `filters`/`prefilter` must fit
    without replacing existing signatures. Record this as a **contract guard**:
  - ✅ Contract guard (1.0): query/search interfaces stay backward-compatible
    with future keyword-only filter params; any 1.x release may only *add*
    optional args, never re-sign.
- **F-2** First external contributor PR as plugin-contract proof (roadmap v0.2/v1.1).
- **F-3** `p99` chunk+embed throughput budget (roadmap v2.0).
- **F-4** Add Python 3.13 to the CI matrix (packaging says `>=3.10`).
- **F-5** Publish a `1.0.0-rc1` and a short soak of the release pipeline before
  tagging the final stable.

---

## History

- 2026-08-20 — created from pre-1.0 architecture review; pyproject Homepage/
  Documentation pointed at `github.com/bzdvdn/winnow`.
- 2026-08-20 — product renamed to **Alvis** (`winnow` → `alvis`, package,
  CLI, docs, instance names); PyPI `winnow` confirmed taken. I-2 now also
  requires claiming/distributing the `alvis` name on PyPI.