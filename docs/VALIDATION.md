# Validation for the Scout increment

- 14 unit/API tests passed using FastAPI 0.115.0 and Databricks SDK 0.153.0.
- The same 14 tests passed against the minimum supported Databricks SDK 0.50.0.
- `node --check apps/bite-atlas/static/scout.js` passed.
- Python compilation and `git diff --check` passed.
- Tests use clearly labeled in-memory fixtures, not records written to Databricks.

Coverage includes schema validation, parameter binding, SQL failure diagnostics,
result chunks, static serving, optional missing sources, ambiguous feature names,
invalid coordinates, occurrence dates/provenance, no fabricated centroid spots,
bank reachability, and driving-to-access versus final casting-position eligibility.

A read-only live Databricks validation was attempted on October 17, 2026. The
previously supplied OAuth credential was rejected with `invalid_client`, so no live
Scout database response was verified and no warehouse/data mutation was performed
by that attempt. Validate in Databricks using the app's current managed identity;
do not put replacement secrets in this repository.

Browser visual testing, live CDN/map tiles, GPS, live routing-provider calls and
Databricks deployment remain to be verified in the target environment using the
checklist in `GENIE_SCOUT_DEPLOY.md`. The routing adapter was tested with deterministic
provider-shaped responses and without a key, not with a paid/live provider account.
