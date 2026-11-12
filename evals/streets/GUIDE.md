# Street eval — labelling guide

Phase 5 spec §5. One row is one street mention VECO typed in an outage's area text
(`area_entities`, `kind = 'street'`). The sample (`eval sample --suite streets --seed 20261112`)
was drawn **before any match rule existed**: 80 rows with distinct `street_key`s, 10 per VECO
town, scope kinds proportional within a town; 30 dev, 50 test.

## What `gold` means

`gold` is the OSM `name` of the way(s) VECO means, **inside or near (≈150 m) the barangays the
outage lists**, or `null` when no such way is in OpenStreetMap.

Labelled against the local extract (`data/osm/`, osm_base `2026-11-12T02:14:58Z`) by looking up
candidates by hand: a read-only helper lists the OSM names of ways with a node inside the
row's scope, and the labeller reads them. The matcher (`streets/match.py`) did not exist yet.

## Conventions

1. **Scope.** The scope is the placed barangays (exact, alias or ambiguous). With none, the
   barangays of the towns the text lists. A way outside the scope is not gold, even if its name
   is identical: it would be drawn in the wrong place.
2. **Same road, different OSM names along its length** (e.g. "Cebu South Road" vs "Natalio B.
   Bacalso Avenue"): gold is the name the way carries **inside the scope**. When both appear in
   scope, the one that matches VECO's wording.
3. **Spelling.** VECO abbreviations and small spelling differences (`St.`, `Ave.`, `Gen.`,
   missing accents, a missing middle initial) are the same road when only one candidate in scope
   could be meant. Gold is written as OSM spells it.
4. **Different road, similar name.** "X Street" vs "X Extension", "X Road" vs "X Street": the
   same road only if OSM has no way with VECO's exact form in scope and the similar one is
   plainly the one meant (same base name, in scope, no competitor). Otherwise `null`.
5. **Generic names** ("Barangay Road", "Main Road", "National Highway", "Brgy. Road"): `null`
   unless OSM has a way with that exact name in scope **and** it is clearly the road meant. A
   named barangay road ("Tiltilon Barangay Road") follows rule 4.
6. **Not a street.** A mention that is really a sitio, subdivision, establishment or a
   description ("along the highway", "near the church"): `null`, with a note.
7. **Unsure** between two candidates in scope: `null` and a note. A wrong line is worse than
   none (spec §1).

Each row may carry a `note` field (free text: why null, which rule). The scorer ignores it.

## Review and freeze

The owner reviews ≥ 15 labels (spread over towns, both splits, nulls and non-nulls). After
corrections, `test.jsonl` is frozen by hash (`FROZEN`) and never opened during rule work; any
later change to it is logged in `CHANGES.md` with the reason.
