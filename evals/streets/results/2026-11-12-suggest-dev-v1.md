# Street suggester — dev — 2026-11-12 — prompt v1

Extract osm_base 2026-11-12T02:14:58Z; 2 aliases; picker `qwen` (qwen3:14b).
Dev rows the rules leave `not_found`: 51 (7 with gold, 44 null).
The rows are few, so the comparison is directional, not a measurement.

- prefilter recall (gold among the 12): 0.857 (6 of 7)
- qwen accuracy: 0.784 (40 of 51)
- difflib accuracy: 0.725 (37 of 51)

| id | VECO name | gold | in top 12 | qwen | difflib |
|---|---|---|---|---|---|
| dev-04 | Cebu-North Coastal Rd. | — | — | none ✓ | none ✓ |
| dev-09 | Cory St. | — | — | none ✓ | Joy Street ✗ |
| dev-10 | Bayong - Yati Road | — | — | none ✓ | Bagong Daan Road ✗ |
| dev-11 | Yakal St. | — | — | none ✓ | Camia Street ✗ |
| dev-12 | of Bowangan St. | — | — | none ✓ | none ✓ |
| dev-16 | Umapad Dumpsite Road | — | — | Umapad Road ✗ | none ✓ |
| dev-17 | Pepito Drive | — | — | Pedro Pepito Street ✗ | none ✓ |
| dev-18 | Brgy. Tonggo Rd. | — | — | none ✓ | none ✓ |
| dev-19 | Yangyang St. | — | — | none ✓ | Panganiban Street ✗ |
| dev-21 | Cebu South Rd Climaoc St. | — | — | Cebu South Road ✗ | none ✓ |
| dev-24 | Sta Cruz St. | — | — | none ✓ | none ✓ |
| dev-27 | Sorsogon Rd. | — | — | none ✓ | Sicogon Road ✗ |
| dev-29 | Selma St. | — | — | G. M. Selma Street ✗ | M.H. Selma Street ✗ |
| dev-30 | Ilaya-San Isidro Road. | — | — | Cebu South Road ✗ | none ✓ |
| test-02 | Polog Road | — | — | none ✓ | none ✓ |
| test-05 | Ottawa St. | Ottawa | yes | Ottawa ✓ | Bogota Street ✗ |
| test-06 | AM Arcilla Road | A.M. Arcilla | yes | A.M. Arcilla ✓ | A.M. Arcilla ✓ |
| test-08 | Tananas Road | — | — | none ✓ | none ✓ |
| test-09 | Fatima Road | — | — | none ✓ | none ✓ |
| test-10 | San Francisco St. | — | — | none ✓ | none ✓ |
| test-12 | Mohon Rd. | — | — | Mojon Road ✗ | Mojon Road ✗ |
| test-13 | Manapsal St | — | — | none ✓ | none ✓ |
| test-14 | Brgy. Mabini Road | — | — | no_candidates ✓ | no_candidates ✓ |
| test-15 | Paradero St. | — | — | none ✓ | none ✓ |
| test-16 | Suba Interior Road. | — | — | none ✓ | none ✓ |
| test-17 | Mendoza Street. | — | — | none ✓ | Mejia Street ✗ |
| test-19 | Mantawi Drive | — | — | none ✓ | none ✓ |
| test-20 | Cebu-Toled Wharf Rd | — | — | none ✓ | none ✓ |
| test-21 | Juan Bastida St. | — | — | none ✓ | none ✓ |
| test-22 | 2nd Street | Second Street | yes | Second Street ✓ | Second Street ✓ |
| test-24 | Brgy. Patag-Brgy. Jaguimit Rd. | — | — | none ✓ | none ✓ |
| test-26 | Abuno Road | — | — | Abuno-Upper Tunghaan Road ✗ | none ✓ |
| test-27 | Tabionan Brgy Rd. | — | — | Cebu South Road ✗ | none ✓ |
| test-28 | Tangke Street | — | — | none ✓ | none ✓ |
| test-30 | De Julio St. | 18 de Julio Street | yes | 18 de Julio Street ✓ | 18 de Julio Street ✓ |
| test-31 | Camino Vicinal Road | — | — | none ✓ | none ✓ |
| test-32 | Juan Luna Avenue extension | — | — | Juan Luna Avenue ✗ | Juan Luna Avenue ✗ |
| test-33 | Villacora St. | — | — | none ✓ | Victor Vega Street ✗ |
| test-34 | Naga-Uling Road | — | — | none ✓ | none ✓ |
| test-35 | Zapanta Street | — | — | none ✓ | Palma Street ✗ |
| test-37 | Horse Shoe Drive | — | — | none ✓ | none ✓ |
| test-38 | Mary Ellen St. | Paseo Mary Ellen | — | Eagle Street ✗ | none ✗ |
| test-39 | Libo Street | — | — | no_candidates ✓ | no_candidates ✓ |
| test-40 | Primavera Hills road. | — | — | none ✓ | none ✓ |
| test-42 | Ilang-ilang Rd. | Ilang-ilang Street | yes | Ilang-ilang Street ✓ | Ilang-ilang Street ✓ |
| test-43 | Consolacion-Tayud-Liloan Road. | — | — | none ✓ | none ✓ |
| test-44 | Sangi St. | — | — | Osmeña Street ✗ | Auring Street ✗ |
| test-45 | Pondel Street | Pondol Street | yes | Pondol Street ✓ | Pondol Street ✓ |
| test-47 | Brgy. Mayana Road | — | — | none ✓ | none ✓ |
| test-48 | Brgy. Bairan Rd. | — | — | no_candidates ✓ | no_candidates ✓ |
| test-50 | Daisy Street | — | — | none ✓ | none ✓ |
