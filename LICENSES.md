# Licences and data notes

This folder is a static snapshot of the Cebu Brownout Tracker, a portfolio demo. It is not
live and not affiliated with VECO (Visayan Electric Company).

- **Outage data:** from VECO's public scheduled power interruption advisories; every outage
  links to its post. Only parsed fields are published (times, status, purpose, the affected
  areas as VECO wrote them, barangays), not the posts themselves.
- **Barangay boundaries:** faeldon/philippines-json-maps v1.1.1 (MIT), PSGC 3Q 2026.
- **Map library:** Leaflet 1.9.4, BSD-2-Clause (in `static/vendor/leaflet-1.9.4/`).
- **Map tiles:** © OpenStreetMap contributors, used under the OSMF tile usage policy. Your
  browser fetches the tiles from OpenStreetMap's servers, which see your IP address.
- **Street lines:** derived from OpenStreetMap data, © OpenStreetMap contributors, available
  under the Open Database License (ODbL) 1.0. Extract as of 2026-11-12T02:14:58Z. The derived lines are
  in `weeks/*.geojson` (the `streets.drawn[].lines` fields) and are offered under the same
  licence.
- **Code:** MIT; see the repository.
