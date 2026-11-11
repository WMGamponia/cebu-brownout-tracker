# Label changes

Every change to `dev.jsonl` or `test.jsonl` after the freeze gets a dated entry here with the
reason, and `test.jsonl` changes update `FROZEN`. Labels are written from the post text, never
from an extractor's output.

- **2026-11-11** — Sample drawn (`eval sample --seed 20261111`), all 60 rows labelled from the
  text following `GUIDE.md`. Owner reviewed all 18 structured rows plus 10 others and approved
  them as labelled (no corrections). `test.jsonl` frozen.
- **2026-11-11** — Phase 3 (owner-approved aliases): `Lipata` (Minglanilla) resolves to Linao
  (PSA `Linao (Linao-Lipata)`) and `Sto. Niño` (Cebu City) to `730600022`, PSA's Santo Niño
  (Poblacion), which the release names `Central`. `test-17` labels Sto. Niño `null` (written
  before this was known); **labels unchanged**, so the rules arm's test `barangay_f1` baseline in
  `baseline.json` drops 1.0 -> 0.9931 (truncated; one false positive against that label).
