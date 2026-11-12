# Street eval — changes

Any change to `test.jsonl` after it is frozen is logged here with the reason and the new `FROZEN` hash.

- 2026-11-12 — sampled (seed 20261112) and labelled; owner reviewed 19 labels (15 judgment calls + 4 plain); all stand. Test split frozen.
- 2026-11-12 — dev-01 (dev only): gold `Central Nautical Highway` → `Cebu North Road`. In OSM the road's `name` is Cebu North Road and Central Nautical Highway is its `alt_name`; the guide makes gold the `name` tag. Found by the first dev run. The test split was labelled the same way and may hold the same kind of slip; it stays frozen and unopened (a slip there can only lower test precision).
- 2026-11-12 — Phase 5 test split retired to `p5/` (run once; folded into dev under GUIDE v2). `p5/FROZEN` still matches `p5/test.jsonl`.
