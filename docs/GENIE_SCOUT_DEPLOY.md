# Copy/paste instructions for Databricks Genie / coding agent

Implement and deploy the GitHub increment in `grumpystrongman/bite-atlas`, branch
`feat/evidence-based-scout`. Read the entire branch before making changes. Preserve
all existing data, ingestion, notebooks, Gold views, Streamlit source, app identity,
and app access controls. Do not overwrite this branch with an older workspace copy.
Do not create synthetic fishing evidence to populate an empty view.

## 1. Synchronize the actual app directory

The deployment source directory must contain these files directly:

- `api.py`, `scout.py`, `scout_routes.py`, `requirements.txt`, `app.yaml`
- `static/scout.html`, `static/scout.css`, `static/scout.js`
- the preserved existing `app.py` and `static/index.html`

They live under `apps/bite-atlas/` in GitHub. Copy/sync the **contents** of that folder
to `/Workspace/Users/cmajeff@gmail.com/bite-atlas-app`; do not accidentally deploy
an extra nested `apps/bite-atlas` directory. Keep secrets outside Git.

The FastAPI startup remains `uvicorn api:app --host=0.0.0.0 --port=8000` as in the
existing app configuration. Preserve Databricks' expected port/startup behavior;
verify actual app environment rather than inventing a new app URL. `/` serves Scout,
`/legacy` preserves the original exported browser page, and APIs remain under `/api/`.
The Streamlit source is preserved but is not started alongside FastAPI by this patch.

## 2. Validate identity and SQL

Use the app's existing managed service principal. It needs CAN USE on the configured
SQL warehouse, USE CATALOG/SCHEMA and SELECT on the specific Gold objects queried.
Set `DATABRICKS_WAREHOUSE_ID` to the existing approved warehouse if different from
the current default. Do not create a warehouse or disable auto-stop.

Required Gold view: `api_nearby_waters`. Optional sources are `api_access_points`,
`api_species_ecology`, `api_fish_attractors`, `habitat_candidates`, and the new
`api_scout_species_occurrence`. Missing optional sources must display as unavailable.
No blanket Bronze/Silver access is needed for the runtime app.

Run the additive migration `sql/scout_contracts.sql` as the authorized ingestion/
schema owner. First inspect existing objects; never replace a populated object.
Create the empty occurrence contract only if needed, then map real existing survey,
stocking, historical catch, and agency evidence into it. Validate canonical species,
waterbody IDs, observed times, original source URLs, coordinate uncertainty, and
spatial associations. A generic species habitat paragraph is not occurrence evidence.
The view must contain only validated rows. Do not mark data validated merely to
increase scores. Keep source-specific ingestion identities and permissions.

## 3. Optional road routing

If an approved openrouteservice key is available, set `ORS_API_KEY` as a server-side
secret. It is optional; do not block deployment or expose the key to JavaScript.
The adapter sends user origin and mapped access coordinates to openrouteservice's
fixed driving-matrix endpoint. Without a key or a route, driving time is unknown.
The map must not claim a 30-minute trip based on radius alone. Routing to an access
point does not establish permission or a walkable/paddleable final approach.

## 4. Test before deployment

From repository root:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node --check apps/bite-atlas/static/scout.js
python -m compileall -q apps/bite-atlas
```

Then test under the real app identity against populated tables:

```json
{
  "latitude":35.994,"longitude":-78.8986,
  "species":["Largemouth Bass","Black Crappie"],
  "target_time":"2026-10-17T18:00:00-04:00",
  "radius_km":40,"max_drive_minutes":30,"mode":"any"
}
```

POST this to `/api/scout` with OAuth. Set the date to the actual intended trip time.
Check water discovery, source coverage, candidate-source coordinates, species evidence,
unknown depth/access/route states, and the absence of centroid-derived casting pins.
In bank mode, zero candidates is valid when reachability evidence is absent; do not
add arbitrary bank coordinates to avoid an empty result.

Test `/`, `/static/scout.js`, `/api/health`, `/api/species`, and `/api/openapi.json` at
the authenticated public app URL. Health only verifies process health, not complete
scouting coverage. Confirm `application/json` for APIs and the new Scout schema.
Test the UI on an iPhone-size viewport, GPS denial, map/CDN failure, empty results,
SQL permission failure, missing optional evidence view, and unavailable routing.
Try the aerial, topo and roads layers; verify 3D land terrain is not labeled depth.

## 5. Deploy and return evidence

Deploy the updated app after successful checks. Return exact source commit, deployment
ID/time, actual authenticated URL test results, coverage counts, and a list of missing
providers/data. GitHub changes alone are not a Databricks deployment. Do not claim
shoreline analysis, sonar, bathymetric holes, parcel access or calibrated catch odds
are complete. Follow `docs/FISHING_INTELLIGENCE_PLAN.md` for those next stages.
