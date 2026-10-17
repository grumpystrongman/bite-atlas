# Bite Atlas: a fishing scout in your pocket

## The intended experience

Open the map, select a few species, choose bank/kayak/boat and a trip time, and say:
“Find my best supported options within 30 minutes.” The app should find the right
water, distinguish a legal approach from a casting location, identify actual
structure, explain why fish may use it at that time, and give a practical gear plan.

The unit of recommendation is a **species + feature + time + approach**, not a pier,
a generic lake marker, or an LLM-generated latitude/longitude.

Each recommendation should answer:

1. Where should I stand or position the boat/kayak, and where should I cast?
2. What species is supported here, by what dated evidence?
3. What feature matters: point, submerged channel edge, hole, creek mouth, shade,
   current seam, vegetation edge, bridge piling, riprap, ledge or mapped attractor?
4. Why this time, temperature/depth band, wind/current/tide and presentation?
5. What lure/bait, size/weight, rig, line/leader, retrieve and adjustment sequence?
6. How do I get there legally: road, parking/launch, walk/paddle and final approach?
7. What is measured, inferred, stale, unavailable, contradictory or personally observed?

A search result can identify an unmapped or unverified bank feature for further
inspection; it must not turn “visible on satellite” into “publicly accessible.”

## What has been implemented in this branch

A new mobile Scout view with aerial/topo/road basemaps, a 3D land-terrain toggle,
multiple target species, timezone-aware trip time, existing Gold water discovery,
feature candidates from stored habitat/attractor data, source/evidence coverage,
optional driving-time matrix to mapped access, and device-local waypoint saving.
The backend explicitly distinguishes discovery centroids from fishing candidates.
General ecology advice is labeled as general. Confidence is capped when evidence
is missing, and priority scores are not catch probabilities.

The first increment does **not** perform imagery analysis, bathymetry processing,
parcel/road permissions, walk/paddle routing, live-condition spot forecasting,
LLM tool orchestration, offline basemap downloads or cross-device waypoint sync.
It does not automatically create/run daily ingestion jobs.

## Layer stack and sources

| Layer | Sources to investigate and ingest | Required treatment |
| --- | --- | --- |
| Water shapes and identity | USGS 3DHP/hydrography, NC OneMap, agency reservoir/river GIS | Canonical IDs, geometry repair, source dates, shoreline changes; centroid is discovery only |
| Aerial context | NC OneMap orthoimagery, USGS/USDA public imagery; separately licensed commercial scenes | Capture date/resolution/license and analysis permission for each asset |
| Land terrain/topography | USGS 3DEP lidar/DEM, NC elevation, terrain tiles | Vertical datum, resolution, land slope; never call bare-earth land DEM lake bathymetry |
| Bathymetry | NOAA NCEI hydrographic surveys, NOAA coastal products, USACE/agency reservoir surveys, licensed lake contours, user sonar | Survey footprint, acquisition date, vertical datum/water level, uncertainty; no inferred deep holes where no survey exists |
| Fisheries biology | NCWRC surveys/stocking/creel studies, NC DMF, USFWS/NOAA fisheries, research papers | Species taxonomy, exact waterbody/spatial scope, observation period, method, bias and confidence |
| Access and parcels | NCWRC access, NC OneMap/county parcels, land managers, parks, USACE/USFS public land and road/trail data | Parcel ownership is not permission; closures, fees, hours, easements, vehicle restrictions and actual entry points |
| Conditions | USGS flow/stage/temp, NWS weather, NOAA CO-OPS tides/currents, USACE reservoir operations, agency DO/clarity samples | Units, observation vs forecast, station distance, forecast valid time, latency and uncertainty |
| Structure | NCWRC attractors; surveyed contours; permitted imagery analysis; user verified reports | Distinguish surveyed/mapped/detected/inferred; geometry association and depth reference |
| Personal observations | Real catch/no-catch logs, gear, effort, sonar and annotated waypoints | User isolation, consent, location privacy, effort normalization; no invented reports |

Source starting points:

- [USGS National Map data](https://www.usgs.gov/the-national-map-data-delivery/gis-data-download)
- [NC OneMap](https://www.nconemap.gov/)
- [NC Wildlife: Where to Fish](https://www.ncwildlife.gov/fishing/where-fish)
- [NOAA bathymetric discovery](https://www.ncei.noaa.gov/maps/bathymetry/)
- [USGS imagery service documentation](https://www.usgs.gov/faqs/what-are-urls-imagery-services-national-map-and-are-they-cached-or-dynamic)
- [MapLibre 3D terrain](https://maplibre.org/maplibre-gl-js/docs/examples/3d-terrain/)

## God's Eye View / Google Earth relationship

Borrow the interaction concept: layered spatial context, smooth 3D exploration,
clickable features, and a conversational tool interface. This branch does not copy
God's Eye View code or its third-party datasets. Its repository's MIT source license
does not grant rights to every imagery/data source it displays.

Google Map Tiles can be an optional visual context provider under the applicable
agreement. Its published policies explicitly prohibit image analysis, machine
interpretation, object detection and geodata extraction from Map Tiles. Do not feed
Google tiles/screenshots into a vision model to build the fishing-feature database.
Use assets with appropriate analysis rights instead, recording those permissions.
Public map viewing does not automatically authorize extraction or offline storage.

- [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view)
- [God's Eye View license/data boundaries](https://github.com/bilawalsidhu/gods-eye-view/blob/main/LICENSE)
- [Google Map Tiles policies](https://developers.google.com/maps/documentation/tile/policies)

## Databricks data model

Preserve the existing Bronze/Silver/Gold tables, knowledge and prediction jobs.
Add contracts incrementally instead of replacing populated schemas.

- **Source registry / coverage:** provider, source URL, allowed use, cadence,
  last successful ingestion, observed/issued time, footprint, resolution, units,
  CRS/vertical datum, license, analysis/offline permissions, and completeness.
- **Waterbody identities:** source IDs/aliases, verified polygons, flow network,
  freshwater/brackish/marine regime with uncertainty; geometry lineage.
- **Species occurrence:** dated observations tied to actual waterbody IDs and
  source evidence. Preserve absence/no-detection separately from no data.
- **Habitat features:** feature ID/type, coordinates/geometry, waterbody link,
  method (survey/vision/derived), confidence, depth/reference, source assets,
  capture/survey time, model version and validation result.
- **Access graph:** legal entry/parking/launch, parcel/easement/closure evidence,
  permitted modes, fees/hours, road/trail/water edges and last verification.
- **Condition snapshots:** observed and forecast fields with valid times and
  source/station distance. Do not substitute seasonal defaults for observations.
- **Scouting runs:** request, candidate set, factor values, uncertainty, source
  versions, model/prompt versions and explanation citations.
- **Personal outcomes:** effort, catch/no-catch, species, presentation, conditions,
  positioning, consent and personal access controls; calibration history.

`sql/scout_contracts.sql` supplies the first additive occurrence contract. The app
can operate without it, but must report unknown species occurrence rather than
fabricate species presence from a global species list.

## Analysis pipeline

1. **Discover water by geometry.** Search shoreline/boundary distance as well as
   centroids; retain river reaches and small waterbodies; deduplicate aliases.
2. **Apply species evidence.** Taxonomy normalization, survey/stocking/catch lineage,
   waterbody association, habitat/salinity constraints, season and uncertainty.
3. **Generate feature candidates.** Use bathymetric derivatives for drop-offs,
   depressions, channels, saddles, humps and breaks only inside survey coverage.
   Use permitted imagery to detect vegetation edges, riprap, docks, shade and
   mouths with capture date, spatial error, visibility and human review.
4. **Validate candidates.** Point/polygon association, plausible depth, duplicate
   suppression, measured-vs-inferred classification and independent spot checks.
5. **Join trip conditions.** Forecast valid time, current direction/speed, water
   stage/temp/DO, weather/wind exposure and target depth. Tag interpolation.
6. **Compute access.** Roads to verified parking/launch; walk or paddle approach;
   route restrictions; arrival time. A 30-minute drive to a ramp does not mean
   30 minutes to the casting position. Do not use road routing as off-road access.
7. **Rank transparently.** Hard exclusions for closures/illegal access and known
   incompatible habitat. Separate habitat suitability, bite-condition match,
   species evidence, access feasibility, source quality and uncertainty.
8. **Explain with bounded LLM tools.** The LLM queries approved data/tools, explains
   factor values, cites original sources, recommends a presentation and states
   alternatives. It must not invent coordinates, current depth, regulations,
   species observations, forecast values or confidence percentages.
9. **Learn from outcomes.** Real effort and no-catch observations, held-out tests,
   species/season/waterbody calibration, comparison against baseline strategies.

## Schedules and freshness

Use real Databricks jobs with retries, incremental checkpoints, pagination,
source health and lineage. Exact cadence must match source publication limits.

- Hourly where supported: weather forecasts, USGS stage/flow/temp, tides/currents,
  reservoir stage/operations; invalidate affected time-specific recommendations.
- Daily: source health, new fisheries/stocking publications, closures/regulations
  changes where published, incremental evidence interpretation and forecast runs.
- Publication/event driven: orthoimagery/lidar/bathymetry/parcel/survey updates.
  Re-downloading static imagery hourly adds expense without new information.
- On demand: small-area feature extraction, trip planning, routing and explanation;
  cache only permitted assets/results with their source-validity metadata.

Schedule pipelines under an ingestion identity; the runtime app generally needs
read access to Gold plus narrowly scoped personal-record writes. A published
cadence label is not proof a job ran successfully. Show last success and coverage.

## Delivery stages and acceptance

1. **This PR: map and evidence foundation.** Unit/API tests pass; app starts;
   authenticated live SQL matches API; optional missing sources are visible.
2. **Reliable NC discovery.** Nearby rivers/lakes/small waters, species evidence,
   free/paid/unknown access, fees/closures and correct approach geometry.
3. **Survey-based underwater structure.** Per-waterbody bathymetry inventory,
   real contour/structure extraction, resolution/datum displays, no invented holes.
4. **Permitted aerial feature analysis.** Documented rights, small-AOI vision tools,
   georeferenced candidates, capture dates and validation against known features.
5. **Trip-time intelligence and routes.** Hourly conditions, full route/arrival
   timeline, geospatial eligibility, sourced gear/rig/presentation adjustments.
6. **Conversational field assistant.** “Why this bank?”, “What if the wind changes?”,
   “Show an alternative for crappie”, save notes and compare recommendations.
7. **Offline and learning.** Licensed offline packs, persisted private waypoints,
   catch/effort sync, real calibration and broader geographic coverage.

Do not advertise calibrated highest-confidence catches until validated against
real outcomes. The useful promise is less scouting work and clearer evidence,
with progressively better decisions as coverage and verification improve.
