# Street eval — changes

Any change to `test.jsonl` after it is frozen is logged here with the reason and the new `FROZEN` hash.

- 2026-11-12 — sampled (seed 20261112) and labelled; owner reviewed 19 labels (15 judgment calls + 4 plain); all stand. Test split frozen.
- 2026-11-12 — dev-01 (dev only): gold `Central Nautical Highway` → `Cebu North Road`. In OSM the road's `name` is Cebu North Road and Central Nautical Highway is its `alt_name`; the guide makes gold the `name` tag. Found by the first dev run. The test split was labelled the same way and may hold the same kind of slip; it stays frozen and unopened (a slip there can only lower test precision).
- 2026-11-12 — Phase 5 test split retired to `p5/` (run once; folded into dev under GUIDE v2). `p5/FROZEN` still matches `p5/test.jsonl`.
- 2026-11-12 — dev rebuilt for Streets v2: the 30 Phase 5 dev rows + the 50 retired Phase 5 test rows (80 rows, `origin` = `p5-dev` / `p5-test`, `osm_base` = `2026-11-12T02:14:58Z`, `town_psgc` added), relabelled under GUIDE v2 from `streets candidates` output (not the matcher). Changed golds:
  - test-18: `Cebu South Road` → `Natalio Bacalso Avenue` (rule 1: the in-scope way's `name`; "Cebu South Road" is its `alt_name`).
  - dev-07: `Maghaway Road` → `null`, note `two roads` (rule 4: a separate Linao Road way in scope also carries `alt_name=Maghaway Road`; the matcher would draw both).
  - All other 78 golds stand. Rule numbers in notes renumbered to GUIDE v2 (v1 rules 3–7 are v2 rules 5–9; v1 rule 1 is "scope").
- 2026-11-12 — Streets v2 test split: 150 rows sampled (seed 20261113, `test.rows.jsonl`, `ROWS_FROZEN` 6ae68462…) and labelled under GUIDE v2 by a fresh labeller from `streets candidates` output (46 null, 104 non-null, 3 lists). Owner reviewed 25 labels (`eval review-sample`); all stand. t2-013 (`Kamatisan Rd.`) discussed: the road exists on the ground but has no name in OSM, so `null` (rule 9) stands — an OSM gap, not a label error. Frozen: `FROZEN` = 0ee44bd8….
