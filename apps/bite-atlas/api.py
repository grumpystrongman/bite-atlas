"""
Bite Atlas — NC Fishing Intelligence REST API
FastAPI backend for Databricks Apps. All endpoints under /api/.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import State

app = FastAPI(
    title="Bite Atlas API",
    description="NC Fishing Intelligence & Predictor — REST endpoints",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

w = WorkspaceClient()
WAREHOUSE_ID = "9e05797dcc5646b3"


def _state_str(state: Any) -> str:
    """Normalise SDK state (enum or plain string) to a comparable string."""
    if state is None:
        return ""
    if isinstance(state, str):
        return state
    return getattr(state, "value", str(state))


def ensure_warehouse() -> None:
    import time
    wh = w.warehouses.get(WAREHOUSE_ID)
    if _state_str(wh.state) == "RUNNING":
        return
    if _state_str(wh.state) in ("STOPPED", "STOPPING"):
        w.warehouses.start(WAREHOUSE_ID)
    for _ in range(30):
        time.sleep(2)
        wh = w.warehouses.get(WAREHOUSE_ID)
        if _state_str(wh.state) == "RUNNING":
            return
    raise RuntimeError(f"Warehouse {WAREHOUSE_ID} did not reach RUNNING within 60 s")


def run_sql(sql: str, timeout: str = "50s") -> list[dict[str, Any]]:
    ensure_warehouse()
    import time

    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout=timeout,
    )
    statement_id = resp.statement_id
    if not statement_id:
        diag = str(uuid.uuid4())[:8]
        raise RuntimeError(f"SQL execution returned no statement ID (diag={diag})")

    # Poll PENDING/RUNNING with a bounded 120 s deadline
    deadline = time.time() + 120
    while resp.status and _state_str(resp.status.state) in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            diag = str(uuid.uuid4())[:8]
            raise RuntimeError(f"SQL statement timed out after 120 s (diag={diag})")
        time.sleep(2)
        resp = w.statement_execution.get_statement(statement_id)

    state = _state_str(resp.status.state) if resp.status else ""

    if state == "FAILED":
        diag = str(uuid.uuid4())[:8]
        err = resp.status.error_message or "SQL execution failed"
        raise RuntimeError(f"{err} (diag={diag})")

    if state == "CANCELED":
        raise RuntimeError("SQL statement was canceled")

    if state != "SUCCEEDED":
        diag = str(uuid.uuid4())[:8]
        raise RuntimeError(f"Unexpected statement state: {state} (diag={diag})")

    if not resp.result:
        return []

    # Collect data including chunked external links
    chunks = list(resp.result.data_array or [])
    ext_links = getattr(resp.result, "external_links", None) or []
    for link in ext_links:
        link_url = getattr(link, "external_link", None) or getattr(link, "link", None)
        if not link_url:
            continue
        import urllib.request
        with urllib.request.urlopen(link_url) as r:
            chunk_data = json.loads(r.read().decode("utf-8"))
            chunks.extend(chunk_data.get("data_array", []))

    if not chunks:
        return []

    cols = [c.name for c in resp.manifest.schema.columns] if resp.manifest else []
    rows: list[dict[str, Any]] = []
    for arr in chunks:
        row: dict[str, Any] = {}
        for i, col in enumerate(cols):
            val = arr[i] if i < len(arr) else None
            if val and isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    pass
            row[col] = val
        rows.append(row)
    return rows


def sql_str(val: Any) -> str:
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


class SearchRequest(BaseModel):
    latitude: float = 35.9
    longitude: float = -79.0
    radius_km: float = 50.0
    mode: str = "any"
    species: str = ""


class UserReportCreate(BaseModel):
    user_id: str = "anonymous"
    waterbody_id: str | None = None
    waterbody_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    species_name: str | None = None
    catch_count: int = 0
    catch_length_cm: float | None = None
    catch_weight_kg: float | None = None
    gear_type: str | None = None
    bait_lure: str | None = None
    depth_ft: int | None = None
    water_temp_c: float | None = None
    clarity: str | None = None
    weather_notes: str | None = None
    notes: str | None = None
    photo_path: str | None = None
    is_catch: bool = True
    effort_hours: float | None = None
    prediction_id: str | None = None


@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "bite-atlas-api", "version": "1.0.0"}


@app.get("/api/species")
def species():
    rows = run_sql("SELECT * FROM bite_atlas.gold.api_species_ecology ORDER BY species_name")
    return JSONResponse(rows)


@app.post("/api/search")
async def search(request: SearchRequest):
    lat = request.latitude
    lon = request.longitude
    radius = request.radius_km
    mode = request.mode.lower()
    species = request.species.strip()

    # Validate mode
    valid_modes = {"any", "bank", "kayak", "boat"}
    if mode not in valid_modes:
        return JSONResponse(
            {"error": f"Invalid mode '{mode}'. Valid: {sorted(valid_modes)}"},
            status_code=400,
        )
    if radius <= 0 or radius > 500:
        return JSONResponse(
            {"error": "radius_km must be between 0 and 500"},
            status_code=400,
        )

    # Mode filter using Gold view count columns
    mode_filter = ""
    if mode == "bank":
        mode_filter = "AND (w.bank_access > 0 OR w.pier_access > 0)"
    elif mode == "kayak":
        mode_filter = "AND w.kayak_access > 0"
    elif mode == "boat":
        mode_filter = "AND w.boat_ramps > 0"

    # Species filter via ecology habitat compatibility
    species_filter = ""
    if species:
        habitat_rows = run_sql(
            f"SELECT habitat FROM bite_atlas.gold.api_species_ecology "
            f"WHERE species_name = {sql_str(species)}"
        )
        if not habitat_rows:
            return JSONResponse(
                {"error": f"Species '{species}' not found in ecology database",
                 "results": []},
                status_code=400,
            )
        habitat = (habitat_rows[0].get("habitat") or "").lower()
        if any(k in habitat for k in ("saltwater", "marine", "coastal", "estuar")):
            species_filter = ("AND w.waterbody_type IN "
                              "('coastal','estuary','ocean','bay','sound','river')")
        elif any(k in habitat for k in ("freshwater", "lake", "river", "stream", "pond")):
            species_filter = ("AND w.waterbody_type IN "
                              "('lake','reservoir','river','stream')")

    dist_expr = (
        f"SQRT(POWER((w.lat - {lat}) * 111.0, 2) + "
        f"POWER((w.lon - {lon}) * 111.0 * COS(RADIANS({lat})), 2))"
    )

    sql = f"""
        SELECT w.waterbody_id, w.canonical_name, w.waterbody_type,
               w.surface_area_acres, w.lat, w.lon,
               w.access_point_count, w.boat_ramps, w.bank_access,
               w.pier_access, w.kayak_access, w.closed_access,
               w.coverage_score,
               {dist_expr} AS distance_km
        FROM bite_atlas.gold.api_nearby_waters w
        WHERE w.lat IS NOT NULL AND w.lon IS NOT NULL
          AND {dist_expr} < {radius}
          {mode_filter}
          {species_filter}
        ORDER BY distance_km
        LIMIT 30
    """
    rows = run_sql(sql)

    # Label access status and enrich with access point details
    for r in rows:
        if r.get("access_point_count", 0) > 0:
            r["access_status"] = "verified"
        else:
            r["access_status"] = "unknown"

    if rows:
        wb_ids = [sql_str(r["waterbody_id"]) for r in rows if r.get("waterbody_id")]
        if wb_ids:
            ap_rows = run_sql(
                f"""SELECT access_id, access_name, waterbody_id, access_type,
                           latitude, longitude, facilities, fees, operator,
                           closure_status, closure_notes
                    FROM bite_atlas.gold.api_access_points
                    WHERE waterbody_id IN ({", ".join(wb_ids)})
                    LIMIT 500"""
            )
            ap_map: dict[str, list] = {}
            for ap in ap_rows:
                wb_id = ap.get("waterbody_id")
                if wb_id:
                    ap_map.setdefault(wb_id, []).append(ap)
            for r in rows:
                r["access_points"] = ap_map.get(r.get("waterbody_id"), [])

    return JSONResponse(rows)


@app.get("/api/predictions")
def predictions(
    target_date: str | None = None,
    waterbody_id: str | None = None,
    species_name: str | None = None,
    fishing_mode: str | None = None,
    include_quarantined: bool = False,
    page: int = 1,
    page_size: int = 50,
):
    # Validate date format
    if target_date:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse(
                {"error": f"Invalid date format: {target_date}. Use YYYY-MM-DD."},
                status_code=400,
            )

    # Validate fishing_mode
    valid_modes = {"freshwater", "saltwater", "fly_fishing", "ice_fishing", "shore"}
    if fishing_mode and fishing_mode.lower() not in valid_modes:
        return JSONResponse(
            {"error": f"Invalid fishing_mode: {fishing_mode}. Valid: {sorted(valid_modes)}"},
            status_code=400,
        )

    # Build filters (qualified with p. for the predictions table)
    filters = ["p.prediction_date >= current_date()"]
    if target_date:
        filters.append(f"p.prediction_date = DATE '{target_date}'")
    if waterbody_id:
        filters.append(f"p.waterbody_id = {sql_str(waterbody_id)}")
    if species_name:
        filters.append(f"p.species_name = {sql_str(species_name)}")
    if fishing_mode:
        filters.append(f"p.fishing_type = {sql_str(fishing_mode.lower())}")

    # Quarantine: exclude saltwater species from freshwater waterbodies
    # All Gold view types (lake, reservoir, river) are freshwater
    if not include_quarantined:
        filters.append(
            "(p.fishing_type != 'saltwater' OR "
            "w.waterbody_type IN ('coastal','estuary','ocean','bay','sound'))"
        )

    # Pagination
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    where_clause = " AND ".join(filters)
    sql = f"""
        SELECT p.prediction_date, p.waterbody_id, p.waterbody_name, p.region,
               p.species_name, p.fishing_type, p.time_window,
               p.time_window_start, p.time_window_end, p.activity_level,
               p.predicted_activity_score, p.confidence_score,
               p.recommended_lure_bait, p.recommended_rig, p.recommended_depth,
               p.recommended_technique, p.llm_explanation, p.water_temp_estimate,
               p.air_temp_forecast, p.wind_speed_forecast, p.moon_phase,
               p.best_access_point, p.best_access_type, p.target_lat, p.target_lon,
               p.evidence_citations, p.uncertainty_notes
        FROM bite_atlas.gold.api_fishing_predictions p
        LEFT JOIN bite_atlas.gold.api_nearby_waters w ON p.waterbody_id = w.waterbody_id
        WHERE {where_clause}
        ORDER BY p.prediction_date ASC, p.waterbody_name ASC,
                 p.species_name ASC, p.time_window_start ASC
        LIMIT {page_size} OFFSET {offset}
    """
    rows = run_sql(sql)

    # Total count for pagination metadata
    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM bite_atlas.gold.api_fishing_predictions p
        LEFT JOIN bite_atlas.gold.api_nearby_waters w ON p.waterbody_id = w.waterbody_id
        WHERE {where_clause}
    """
    count_rows = run_sql(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    return JSONResponse({
        "predictions": rows,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    })


@app.get("/api/plans")
def plans():
    rows = run_sql(
        """SELECT plan_id, waterbody_id, target_date, target_species,
                  fishing_mode, recommended_species, recommended_windows,
                  lure_bait_strategy, rig_tackle, retrieve_depth_strategy,
                  depth_strategy, rationale, citations, uncertainty_notes,
                  missing_data, is_forecast_based, plan_type, created_at
           FROM bite_atlas.gold.fishing_plans
           ORDER BY created_at DESC
           LIMIT 100"""
    )
    return JSONResponse(rows)


@app.get("/api/map_layers")
def map_layers():
    waterbodies = run_sql(
        """SELECT waterbody_id, canonical_name, waterbody_type,
                  surface_area_acres, lat, lon, access_point_count,
                  boat_ramps, bank_access, pier_access, kayak_access,
                  coverage_score
           FROM bite_atlas.gold.api_nearby_waters
           LIMIT 200"""
    )
    predictions = run_sql(
        """SELECT waterbody_id, waterbody_name, latitude, longitude,
                  species_name, activity_level, predicted_activity_score,
                  prediction_date, location_name, recommended_technique
           FROM bite_atlas.gold.api_prediction_locations
           WHERE prediction_date >= current_date()
           LIMIT 100"""
    )
    return JSONResponse({"waterbodies": waterbodies, "predictions": predictions})


@app.get("/api/user_reports")
def user_reports():
    rows = run_sql(
        """SELECT report_id, user_id, report_datetime, waterbody_id,
                  waterbody_name, latitude, longitude, species_name,
                  catch_count, catch_length_cm, catch_weight_kg,
                  gear_type, bait_lure, depth_ft, water_temp_c,
                  clarity, weather_notes, notes, photo_path,
                  is_catch, effort_hours, prediction_id, created_at
           FROM bite_atlas.gold.api_user_reports
           ORDER BY report_datetime DESC
           LIMIT 100"""
    )
    return JSONResponse(rows)


@app.post("/api/user_reports")
async def create_user_report(report: UserReportCreate):
    report_id = str(uuid.uuid4())
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cols = [
        "report_id", "user_id", "report_datetime", "waterbody_id",
        "waterbody_name", "latitude", "longitude", "species_name",
        "catch_count", "catch_length_cm", "catch_weight_kg",
        "gear_type", "bait_lure", "depth_ft", "water_temp_c",
        "clarity", "weather_notes", "notes", "photo_path",
        "is_catch", "effort_hours", "prediction_id", "created_at",
    ]
    vals = [
        sql_str(report_id),
        sql_str(report.user_id),
        f"TIMESTAMP '{now}'",
        sql_str(report.waterbody_id),
        sql_str(report.waterbody_name),
        str(report.latitude) if report.latitude is not None else "NULL",
        str(report.longitude) if report.longitude is not None else "NULL",
        sql_str(report.species_name),
        str(report.catch_count),
        str(report.catch_length_cm) if report.catch_length_cm is not None else "NULL",
        str(report.catch_weight_kg) if report.catch_weight_kg is not None else "NULL",
        sql_str(report.gear_type),
        sql_str(report.bait_lure),
        str(report.depth_ft) if report.depth_ft is not None else "NULL",
        str(report.water_temp_c) if report.water_temp_c is not None else "NULL",
        sql_str(report.clarity),
        sql_str(report.weather_notes),
        sql_str(report.notes),
        sql_str(report.photo_path),
        "TRUE" if report.is_catch else "FALSE",
        str(report.effort_hours) if report.effort_hours is not None else "NULL",
        sql_str(report.prediction_id),
        f"TIMESTAMP '{now}'",
    ]

    sql = (
        f"INSERT INTO bite_atlas.gold.user_reports ({', '.join(cols)})\n"
        f"VALUES ({', '.join(vals)})"
    )
    run_sql(sql, timeout="30s")
    return JSONResponse(
        {"report_id": report_id, "status": "created", "message": "Report submitted successfully"},
        status_code=201,
    )


@app.get("/api/openapi.json")
def openapi():
    return app.openapi()
