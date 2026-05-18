# SOP corpus

Drop SOP documents in this directory (any depth). Supported formats:

| Suffix  | Reader                          |
|---------|---------------------------------|
| `.txt`  | UTF-8 plain text                |
| `.md`   | UTF-8 markdown                  |
| `.pdf`  | `pdfplumber` (already a dep)    |
| `.docx` | `python-docx` (optional dep — install with `poetry add python-docx` if you have Word documents)

Unknown formats are skipped with a log warning, not an error.

## Ingest

```bash
poetry run python -m memory.ingest_sop docs/sop/
```

Re-running is idempotent — chunk IDs are content-hashed so unchanged files
upsert into the same rows. Use `--reset` to drop and rebuild the collection
(needed only when you rename files or change chunking parameters).

## How agents use it

`ActionPlanner.decide()` calls `knowledge.query_sop(goal + screen_summary, k=2)`
and prepends the top hits as a `system` message before the planning prompt.
SOP retrieval is best-effort: any failure (chromadb unavailable, query
exception) is swallowed and the agent plans without SOP context. So shipping
without an SOP corpus still works — agents simply plan with no policy hints.

## Scoping

This release uses a single org-wide collection. To add per-client overlays
later, set a `client_id` in chunk metadata at ingest time and pass
`where={"client_id": settings.client_id}` to `query_sop`. The collection
schema does not change.

## Design tradeoffs (read before changing this layer)

The full rationale lives in [docs/architecture-hybrid-runtime.md §11](../architecture-hybrid-runtime.md#11-sop-memory--implementation-tradeoffs-2026-05-14). Short version:

- **Single embedder** (`all-MiniLM-L6-v2`) — switching embedders requires `--reset` + re-ingest.
- **Best-effort retrieval** — SOP query failures never block planning; check the `sop_query_failed` log line.
- **Org-wide scope** — add `client_id` filtering before any multi-tenant install.
- **Injection every plan** — `~2×` SOP token cost; cache once-per-task if it becomes expensive.
- **Content-hashed IDs** — idempotent re-ingest; rename = orphan rows (use `--reset`).
- **Character chunking** — not tiktoken-exact; fine for retrieval, planner caps `max_tokens` anyway.
- **Tests need `--with phase4`** for the real Chroma path; the Null-store path covers contracts.

## Maintenance

- Re-run ingest after any SOP update. The next agent task will pick up the
  fresh content.
- Embeddings use ChromaDB's default (`all-MiniLM-L6-v2`). Switching the
  embedder requires `--reset` + re-ingest because the vector spaces aren't
  compatible.
- Keep individual files under ~50 pages. Larger PDFs work but ingest is slower
  and retrieval surfaces less-specific chunks.
