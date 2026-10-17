# Bite Atlas

North Carolina fishing reconnaissance on Databricks: map context, selected species,
mapped structure, evidence coverage, and access planning in a mobile browser.

The new **Scout** UI is served at `/`; the original exported browser UI is preserved
at `/legacy` (its existing endpoint mismatches are not repaired by this increment).
The Streamlit source remains unchanged at `apps/bite-atlas/app.py`; the current
`app.yaml` runs FastAPI, not a second Streamlit process.

## Run locally

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# Configure authorized Databricks credentials through your environment/profile.
cd apps/bite-atlas
../../.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000
```

Static pages and OpenAPI can load without credentials; data requests need them.
On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\pip.exe`.

## Check

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check apps/bite-atlas/static/scout.js
```

## What this increment does

- Aerial, USGS topographic, and road basemaps with optional 3D land terrain.
- Up to five target species and a timezone-aware trip time.
- Nearby water discovery through existing Gold views.
- Candidate points from actual mapped fish attractors and stored habitat candidates.
- Separate species occurrence evidence, general ecology, depth reference, access
  associations, evidence-priority factors, and missing-data disclosures.
- Bank candidates require supplied bank-reachability evidence. No invented shores,
  holes, depth soundings, road routes, or catch probabilities.
- Optional `ORS_API_KEY` enables driving-time estimates **to mapped access points**;
  walking/off-road/water routes and the final approach remain unverified.
- GeoJSON export and device-local waypoint saving (no cross-device sync yet).
- SQL failures produce diagnostic errors, with inline result-chunk handling.

## Boundaries

This is the first map/scouting increment, not a completed global fishing guide.
It does not analyze aerial pixels, derive underwater features, generate live-condition
bite predictions, calibrate catch odds, download offline maps, or establish landowner
permission. Existing cached prediction tables are not treated as species-occurrence
proof. Trip time currently sets the evidence cutoff; current conditions are explicitly
unverified and are not credited in ranking. Species choices label evidence-backed or
unknown discovery; they do not imply fish are present at every candidate.

The new occurrence view is optional: until real evidence is loaded using
`sql/scout_contracts.sql`, occurrence remains unknown and confidence remains low.
Optional source failures are visible, not silently converted into supported findings.
Feature counts are bounded; see response source coverage and candidate limits.

Deployment: [docs/GENIE_SCOUT_DEPLOY.md](docs/GENIE_SCOUT_DEPLOY.md).
Product/data plan: [docs/FISHING_INTELLIGENCE_PLAN.md](docs/FISHING_INTELLIGENCE_PLAN.md).
