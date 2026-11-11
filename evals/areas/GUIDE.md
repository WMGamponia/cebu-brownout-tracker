# Area labelling guide

How the gold labels in `dev.jsonl` and `test.jsonl` are written. Labels come from the post text
(`areas_raw`) only, never from any extractor's output. Barangay codes come from
`src/outages/areas/barangays.csv` (PSGC, `faeldon/philippines-json-maps` v1.1.1).

Every arm's output goes through the same `normalize_entity()` before scoring, so the conventions
below are the contract between labels and code. Scoring compares a multiset of
`(kind, name)` per string, casefolded, whitespace collapsed, trailing `.` dropped.

## Row shape

```json
{"id": "dev-01", "areas_hash": "...", "slug": "...", "areas_raw": "...",
 "towns": ["Mandaue City"],
 "barangays": [{"name": "Umapad", "psgc_code": "731300027"}],
 "entities": [{"kind": "street", "name": "U.N. Ave."}]}
```

## Towns

The towns named in the town position (`Portion of <town> (…)`, `…, <town>, along`, the first
line of a structured post), as the canonical names: `Cebu City`, `Mandaue City`, `Talisay City`,
`City of Naga`, `Minglanilla`, `Consolacion`, `Liloan`, `San Fernando`.

## Barangays

- Only names in the barangay position: inside the first `( … )` of a city-first line, before the
  town in a barangay-first line, or after `Barangay(s):`. A barangay name used elsewhere
  (`Sitios Balud`, `Parian Brgy Hall`) is not a barangay mention.
- `name` is the name as posted (without surrounding spaces or a trailing `.`); `psgc_code` is
  the code of the barangay it means, within the listed towns, even when the spelling differs
  (`Ward I` → Poblacion Ward I, `Calajoan` → Calajo-an).
- If the name exists in two listed towns and the post doesn't say which (`Tayud` under
  `Consolacion & Liloan`), list it once per town, each with its code. A missed alert is worse
  than two.
- A real barangay missing from the gazetteer: `psgc_code: null`, name kept.
- A name in the barangay position that is not a barangay at all (`South Reclamation Area`,
  `North Reclamation Area`, business parks) is not listed under `barangays`; it is an entity
  of kind `other`.

## Entity kinds

| kind | what |
|---|---|
| `street` | streets, roads, avenues, highways, boulevards, drives, extensions, bypasses |
| `sitio_purok` | sitios, puroks, zones, and named parts of a barangay such as `Upper Tingub`, `Lower Calajoan`, `Proper` |
| `subdivision` | subdivisions, villages, compounds, homes, residences, townhouses, residential hills and condominiums |
| `establishment` | companies, institutions, schools, churches and chapels, halls and offices, stations, plants, markets, stores, associations |
| `other` | any other named place, or a name whose kind the text doesn't show |

**How the kind is decided:**
1. In a structured post, a sub-label that names one kind decides it for every value under it
   (`Streets:` → street, `Sitios:`/`Puroks:`/`Puroks/Sitios:` → sitio_purok,
   `Subdivisions/Villages/Compounds:`/`Compounds / Residential Areas:` → subdivision,
   `Establishments:`/`Schools:`/`Religious Sites:`/`Government Facilities:`/
   `Homeowners Associations:` → establishment). Under a label that mixes kinds
   (`Sitios / Establishments:`), each name gets its own kind.
2. In free text, each name gets the kind of what it is, read from the name (`… St.` is a
   street, `… Inc.` an establishment) and from the list it sits in: a run introduced by
   `Sitios`/`Puroks` stays sitio_purok until a name of another kind appears; a run of
   residential names (`… Subdivision, … Homes, St. Theresa Hills, …`) stays subdivision.
3. If neither the name nor its list tells the kind, it is `other`.

## Names

- **Cue words and connectives are dropped:** `along`, `including`, `portion(s) of`,
  `portions along`, `as well as`, `specifically at`, `road going to`, a leading `and`.
- **A type prefix is dropped when a name follows:** `Puroks Mangga` → `Mangga`,
  `Sitio Kawayan` → `Kawayan`, `Purok Tambis` → `Tambis`. It is kept when a number follows,
  because the number alone is not a name: `Purok 3`, `Purok 2 Dela Paz`, `M1 Purok 6`.
  `Zone` is always part of the name.
- **Number lists expand:** `Zone 3,4,5` → `Zone 3`, `Zone 4`, `Zone 5`;
  `Villa Lorena 1 & 2` → `Villa Lorena 1`, `Villa Lorena 2`. A shared head noun expands too:
  `Lower & Central Calajoan` → `Lower Calajoan`, `Central Calajoan`.
- **Intersections split:** `SRP corner F. Vestil Street`, `Cor. J. Luna & Osmena Avenue` →
  each street on its own; `corner`/`cor.` is dropped.
- **Otherwise the name stays as written**, typos and abbreviations included:
  `Cebu South Road Properties` (never shortened to `Cebu South Road`), `F. Ramas Extension`,
  `Ramcar Incorporated`, `Osmena`. A truncated or merged name is wrong.
- **A company suffix after a comma belongs to the name:** `Wilcon Depot, Inc.` is one name.
- **`&` inside a name stays:** `Advance Graphics & Concepts`; `&` between names splits them.
- **A parenthetical that qualifies a name stays with it:** `SM Prime Holdings, Inc. (Seaside)`.
- **Repeats are kept:** a name listed twice is two entities (scoring uses multisets).

## Worked examples (dev)

**dev-09** `Portion of Mandaue City (Labogon) including portion of Sitio Chicos, Proper and Tabay.`
→ towns `Mandaue City`; barangays `Labogon` (731300015); entities sitio_purok `Chicos`,
`Proper`, `Tabay` (the `Sitio` run continues through `Proper and Tabay`).

**dev-05** `Portion of Cadulawan, Ward I, Ward IV & Tunghaan, Minglanilla, along portion of
Tres De Mayo Street & Bayong Road.` → towns `Minglanilla`; barangays `Cadulawan`, `Ward I`
(Poblacion Ward I), `Ward IV` (Poblacion Ward IV), `Tunghaan`; entities street
`Tres De Mayo Street`, `Bayong Road`.

**dev-20** `Portion of Cebu City (South Reclamation Area) including portion along SRP Bypass
Road.` → towns `Cebu City`; no barangays; entities other `South Reclamation Area`, street
`SRP Bypass Road`.
