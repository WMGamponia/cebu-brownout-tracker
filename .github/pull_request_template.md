## Summary
<!-- What changes, in a sentence or two. Name the phase / ROADMAP step. -->

## Changes
<!-- By file path, grouped by area (ingest, parse, storage, CLI, docs). -->
- `path/to/file` — what changed

## Results
<!-- Only for changes that touch stored data. Before → after; drop rows that don't apply. -->
| Metric | Before | After |
|---|---|---|
| Posts / outages stored | | |
| Posts failed | | |
| Warnings | | |

## Testing
- [ ] `ruff check` and `ruff format --check` pass
- [ ] `pytest -q` passes; tests added/updated for changed behavior
- [ ] Parser changes: regression test from the real post; golden files reviewed line by line
- [ ] `pytest -m live` run locally (if fetching or parsing changed)

## Roadmap
- [ ] `ROADMAP.md` updated (status, progress log, known issues, lessons)
- [ ] No new dependencies (or explained above)
- [ ] Nothing from `data/` or `.env` committed
