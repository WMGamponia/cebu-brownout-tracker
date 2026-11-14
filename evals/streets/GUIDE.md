# Street eval — labelling guide (v2)

v2, 2026-11-12; v1 rows are in `p5/` (Phase 5: 30 dev + 50 test, run once). Streets v2 spec
§5.1. One row is one street mention VECO typed in an outage's area text (`area_entities`,
`kind = 'street'`).

- **dev** (`dev.jsonl`, 80 rows): the 30 Phase 5 dev rows and the 50 retired Phase 5 test rows,
  relabelled under this guide (`origin` = `p5-dev` / `p5-test`; changes in `CHANGES.md`).
- **test** (`test.rows.jsonl` → `test.jsonl`, 150 rows): sampled by
  `eval sample --suite streets-v2 --seed 20261113` — mentions drawn uniformly over the corpus,
  one row per (town, `street_key`), the 80 dev keys excluded — before the suggester existed,
  labelled by a fresh labeller who never saw the matcher, frozen by hash (`FROZEN`).

## What `gold` means

`gold` is the OSM name of the way(s) VECO means, **inside the scope + 150 m**, or `null`.

The scope: the placed barangays (exact, alias or ambiguous); with none, every barangay of the
towns the text lists. A way outside the scope is never gold, even with an identical name: it
would be drawn in the wrong place.

Label from the candidates helper, not the matcher:

    python -m outages streets candidates --rows <rows.jsonl>

It lists, per row, every way in scope (≥ 2 nodes inside scope + 150 m) with all its name tags
and a way count. Labelled against the local extract (`data/osm/`, osm_base
`2026-11-12T02:14:58Z`); each row records it.

## Rules

1. **The name read is the way's `name` tag**, whichever tag VECO's wording happens to match.
   VECO's "Cebu South Road" in Minglanilla → gold `Natalio Bacalso Avenue` if that is the
   in-scope way's `name` (and "Cebu South Road" its `alt_name`). Gold and the matcher read the
   same tag, so "right line, other name" cannot occur.
2. **No `name` tag:** the first present of `official_name`, `alt_name`, `old_name`,
   `short_name` (the helper prints it first as the way's primary name). `name:en` is not
   indexed and is ignored.
3. **A road whose `name` changes inside the scope:** gold is a **list** of every `name` the
   meant way(s) carry in scope, e.g. `["Natalio Bacalso Avenue", "Cebu South Road"]`.
4. **Two different roads in scope carry VECO's name and only one is meant:** `null`, note
   `two roads`. The matcher would draw both; half the drawing is wrong, so it counts as a wrong
   line.
5. **Spelling.** VECO abbreviations and small spelling differences (`St.`, `Ave.`, `Gen.`,
   missing accents, a missing middle initial) are the same road when only one candidate in
   scope could be meant. Gold is written as OSM spells it.
6. **Different road, similar name.** "X Street" vs "X Extension", "X Road" vs "X Street": the
   same road only if OSM has no way with VECO's exact form in scope and the similar one is
   plainly the one meant (same base name, in scope, no competitor). Otherwise `null`.
7. **Generic names** ("Barangay Road", "Main Road", "National Highway", "Brgy. Road"): `null`
   unless OSM has a way with that exact name in scope **and** it is clearly the road meant. A
   named barangay road ("Tiltilon Barangay Road") follows rule 6.
8. **Not a street.** A mention that is really a sitio, subdivision, establishment or a
   description ("along the highway", "near the church"): `null`, with a note.
9. **Unsure** between candidates in scope: `null` and a note. A wrong line is worse than none.

Rules 5–9 are Phase 5's rules 3–7, unchanged.

## Row format

Every row keeps its sampled fields (`id`, `areas_hash`, `seq`, `name`, `areas_raw`, `towns`,
`barangays`, `town_psgc`) and adds:

- `gold`: a string, a list of strings, or `null`;
- `osm_base`: the extract's (`2026-11-12T02:14:58Z`); the eval refuses any other;
- `note` (optional): free text — why null, which rule. The scorer ignores it.

## Scoring

A drawn row is correct when the `street_key` of the matcher's `osm_name` (the most common
primary name of the matched ways, rule 2) is among the gold keys. Precision = correct drawn /
drawn; coverage = correct drawn / rows with non-null gold.

## Review and freeze

The owner reviews ≥ 25 test labels (`eval review-sample --suite streets-v2 --n 25`, spread over
towns, null and non-null, lists and `two roads`). After corrections, `test.jsonl` is frozen by
hash (`FROZEN`) and never opened again until the single test run; any later change is logged in
`CHANGES.md` with the reason.
