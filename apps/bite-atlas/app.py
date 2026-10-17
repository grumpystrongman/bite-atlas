import streamlit as st
import pandas as pd
import json, uuid, math, io
from datetime import datetime, date
from databricks.sdk import WorkspaceClient

# --- Page config ---
st.set_page_config(page_title="Bite Atlas", page_icon="🎣", layout="wide", 
                   initial_sidebar_state="expanded",
                   menu_items={'About': 'Bite Atlas — NC Fishing Intelligence & Predictor'})

# --- Theme ---
st.markdown('''
<style>
:root{--forest:#1b4332;--teal:#2d6a4f;--light-teal:#52b788;--orange:#e76f51}
.stApp{background:#f8f9fa}
.stButton>button{background:var(--orange);color:#fff;border:none;border-radius:8px}
.stButton>button:hover{background:#d65a3e;color:#fff}
.stTabs [data-baseweb="tab-list"]{gap:4px}
.stTabs [data-baseweb="tab"]{padding:8px 12px;font-size:14px}
section[data-testid="stSidebar"]{background:#1b4332}
section[data-testid="stSidebar"] .stMarkdown{color:#fff}
.metric-card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.prediction-card{border-left:4px solid #52b788;padding:12px;margin-bottom:8px;background:#f0f7f4;border-radius:0 8px 8px 0}
</style>
''', unsafe_allow_html=True)

# --- Databricks client ---
@st.cache_resource
def get_client():
    return WorkspaceClient()

w = get_client()
_warehouse_id = None

def get_warehouse():
    global _warehouse_id
    if _warehouse_id:
        return _warehouse_id
    for wh in w.warehouses.list():
        if wh.state == 'RUNNING':
            _warehouse_id = wh.id
            return _warehouse_id
    for wh in w.warehouses.list():
        if wh.id:
            _warehouse_id = wh.id
            return wh.id
    return None

def run_sql(sql: str) -> pd.DataFrame:
    wh = get_warehouse()
    if not wh:
        st.error("No SQL warehouse found")
        return pd.DataFrame()
    resp = w.statement_execution.execute_statement(warehouse_id=wh, statement=sql, wait_timeout='50s')
    if resp.status and resp.status.state == 'FAILED':
        st.error(f"SQL failed: {resp.status.error_message or 'unknown'}")
        return pd.DataFrame()
    if not resp.result or not resp.result.data_array:
        return pd.DataFrame()
    cols = [c.name for c in resp.manifest.schema.columns] if resp.manifest else []
    rows = []
    for arr in resp.result.data_array:
        row = {}
        for i, c in enumerate(cols):
            v = arr[i]
            if v and isinstance(v, str):
                try: v = json.loads(v)
                except: pass
            row[c] = v
        rows.append(row)
    return pd.DataFrame(rows)

def sql_str(val) -> str:
    if val is None: return "NULL"
    return "'" + str(val).replace("'", "''") + "'"

# --- Header ---
st.markdown("# 🎣 Bite Atlas")
st.markdown("*NC Fishing Intelligence & Predictor — What to catch, when, where, and why*")
st.divider()

# --- Navigation ---
tab_explore, tab_map, tab_plans, tab_notes, tab_science = st.tabs([
    "🔍 Explore", "🗺️ Map", "📋 Plans", "📝 Notes", "🔬 Science"
])

# ==================== EXPLORE ====================
with tab_explore:
    st.markdown("### Find Waters Near You")
    col1, col2, col3 = st.columns(3)
    with col1:
        lat = st.number_input("Latitude", value=35.9, step=0.01, key="exp_lat")
    with col2:
        lon = st.number_input("Longitude", value=-79.0, step=0.01, key="exp_lon")
    with col3:
        radius = st.number_input("Radius (km)", value=50, step=5, key="exp_radius")
    
    colm1, colm2 = st.columns(2)
    with colm1:
        mode = st.selectbox("Fishing Mode", ["Any", "Bank", "Kayak", "Boat"], key="exp_mode")
    with colm2:
        target_species = st.text_input("Target Species (optional)", key="exp_species")
    
    if st.button("Search Waters", key="exp_search"):
        mode_filter = ""
        mode_map = {"Bank": "bank", "Kayak": "kayak", "Boat": "boat"}
        if mode in mode_map:
            m = mode_map[mode]
            if m == "bank": mode_filter = "AND ap.access_type IN ('boat_ramp','pier','bank_fishing')"
            elif m == "kayak": mode_filter = "AND ap.access_type IN ('boat_ramp','kayak_launch','paddle_access')"
            elif m == "boat": mode_filter = "AND ap.access_type = 'boat_ramp'"
        
        sql = f"""
            SELECT w.waterbody_id, w.canonical_name, w.waterbody_type, 
                   w.surface_area_acres, w.centroid_lat, w.centroid_lon,
                   SQRT(POWER((w.centroid_lat - {lat}) * 111.0, 2) + 
                        POWER((w.centroid_lon - {lon}) * 111.0 * COS(RADIANS({lat})), 2)) as distance_km,
                   COUNT(DISTINCT ap.access_id) as access_count
            FROM bite_atlas.silver.waterbodies w
            LEFT JOIN bite_atlas.silver.access_points ap ON w.waterbody_id = ap.waterbody_id
            WHERE w.centroid_lat IS NOT NULL AND w.surface_area_acres > 50
            AND SQRT(POWER((w.centroid_lat - {lat}) * 111.0, 2) + 
                     POWER((w.centroid_lon - {lon}) * 111.0 * COS(RADIANS({lat})), 2)) < {radius}
            {mode_filter}
            GROUP BY w.waterbody_id, w.canonical_name, w.waterbody_type, w.surface_area_acres, 
                     w.centroid_lat, w.centroid_lon
            ORDER BY distance_km LIMIT 30
        """
        df = run_sql(sql)
        if df.empty:
            st.info("No waterbodies found. Try expanding your search radius.")
        else:
            st.success(f"Found {len(df)} waterbodies within {radius}km")
            for _, wb in df.iterrows():
                with st.expander(f"🌊 {wb['canonical_name']} ({wb['distance_km']:.1f}km)"):
                    col_a, col_b, col_c = st.columns(3)
                    col_a.metric("Acres", int(wb['surface_area_acres']) if wb['surface_area_acres'] else 0)
                    col_b.metric("Access Points", wb['access_count'])
                    col_c.metric("Distance", f"{wb['distance_km']:.1f}km")
                    
                    preds = run_sql(f"""
                        SELECT species_name, activity_level, predicted_activity_score,
                               recommended_lure_bait, recommended_rig, recommended_depth,
                               llm_explanation, time_window, water_temp_estimate
                        FROM bite_atlas.gold.api_fishing_predictions
                        WHERE waterbody_id = {sql_str(wb['waterbody_id'])}
                        AND prediction_date >= current_date()
                        ORDER BY predicted_activity_score DESC LIMIT 5
                    """)
                    if not preds.empty:
                        st.markdown("**Top Predictions:**")
                        for _, p in preds.iterrows():
                            score = p.get('predicted_activity_score', 0)
                            level = p.get('activity_level', 'unknown')
                            color = '#d4edda' if level == 'high' else '#fff3cd' if level == 'moderate' else '#f8d7da'
                            st.markdown(f"""
                            <div class="prediction-card">
                            <strong>{p['species_name']}</strong> 
                            <span style="background:{color};padding:2px 8px;border-radius:10px;font-size:11px">{level}</span><br>
                            <small>{p.get('time_window','')} • Water est {int(p.get('water_temp_estimate',0) or 0)}°F</small><br>
                            <small><strong>Use:</strong> {p.get('recommended_lure_bait','N/A')} • {p.get('recommended_rig','')} • {p.get('recommended_depth','')}</small>
                            </div>
                            """, unsafe_allow_html=True)
                            if p.get('llm_explanation'):
                                st.info(p['llm_explanation'][:300])
                    else:
                        st.caption("No predictions available (weather forecast needed)")

# ==================== MAP ====================
with tab_map:
    st.markdown("### NC Fishing Map")
    
    map_lat = st.number_input("Center Latitude", value=35.5, key="map_lat")
    map_lon = st.number_input("Center Longitude", value=-79.0, key="map_lon")
    map_radius = st.number_input("Radius (km)", value=80, key="map_radius")
    
    if st.button("Load Map Data", key="map_load"):
        wb_df = run_sql(f"""
            SELECT canonical_name, centroid_lat, centroid_lon, surface_area_acres,
                   SQRT(POWER((centroid_lat - {map_lat}) * 111.0, 2) + 
                        POWER((centroid_lon - {map_lon}) * 111.0 * COS(RADIANS({map_lat})), 2)) as distance_km
            FROM bite_atlas.silver.waterbodies
            WHERE centroid_lat IS NOT NULL AND surface_area_acres > 20
            AND SQRT(POWER((centroid_lat - {map_lat}) * 111.0, 2) + 
                     POWER((centroid_lon - {map_lon}) * 111.0 * COS(RADIANS({map_lat})), 2)) < {map_radius}
            ORDER BY distance_km LIMIT 200
        """)
        if not wb_df.empty:
            st.success(f"{len(wb_df)} waterbodies loaded")
            
            # Simple folium map
            try:
                import folium
                from streamlit_folium import st_folium
                m = folium.Map(location=[map_lat, map_lon], zoom_start=8)
                for _, wb in wb_df.iterrows():
                    folium.CircleMarker(
                        location=[wb['centroid_lat'], wb['centroid_lon']],
                        radius=max(3, min(10, wb['surface_area_acres']/1000)),
                        popup=f"{wb['canonical_name']} ({int(wb['surface_area_acres'])} acres)",
                        color='#2d6a4f', fill=True, fillColor='#52b788', fillOpacity=0.6
                    ).add_to(m)
                st_folium(m, width=700, height=400)
            except ImportError:
                st.dataframe(wb_df[['canonical_name','surface_area_acres','distance_km']])
        else:
            st.info("No waterbodies found in this area")
    
    st.markdown("#### Available Map Layers")
    layers = [("Water Bodies", 8634), ("Access Points", 528), ("Fish Attractors", 1009),
             ("Trout Streams", 1807), ("Navigation Aids", 5488), ("Paddle Access", 51),
             ("Ocean Bathymetry", 62), ("Terrain Elevation", 141)]
    cols = st.columns(4)
    for i, (name, count) in enumerate(layers):
        cols[i % 4].metric(name, f"{count:,}")

# ==================== PLANS ====================
with tab_plans:
    st.markdown("### Fishing Trip Planner")
    
    plan_lat = st.number_input("Latitude", value=35.9, key="plan_lat")
    plan_lon = st.number_input("Longitude", value=-79.0, key="plan_lon")
    
    if st.button("Load Nearby Waterbodies", key="plan_load"):
        wb_df = run_sql(f"""
            SELECT waterbody_id, canonical_name, surface_area_acres,
                   SQRT(POWER((centroid_lat - {plan_lat}) * 111.0, 2) + 
                        POWER((centroid_lon - {plan_lon}) * 111.0 * COS(RADIANS({plan_lat})), 2)) as distance_km
            FROM bite_atlas.silver.waterbodies
            WHERE centroid_lat IS NOT NULL AND surface_area_acres > 50
            AND SQRT(POWER((centroid_lat - {plan_lat}) * 111.0, 2) + 
                     POWER((centroid_lon - {plan_lon}) * 111.0 * COS(RADIANS({plan_lat})), 2)) < 100
            ORDER BY distance_km LIMIT 30
        """)
        st.session_state['plan_wbs'] = wb_df
    
    if 'plan_wbs' in st.session_state and not st.session_state['plan_wbs'].empty:
        wb_options = st.session_state['plan_wbs']
        selected = st.selectbox("Select Waterbody", 
            options=range(len(wb_options)),
            format_func=lambda i: f"{wb_options.iloc[i]['canonical_name']} ({wb_options.iloc[i]['distance_km']:.1f}km)",
            key="plan_select")
        
        if selected is not None:
            wb_id = wb_options.iloc[selected]['waterbody_id']
            wb_name = wb_options.iloc[selected]['canonical_name']
            
            preds = run_sql(f"""
                SELECT prediction_id, prediction_date, species_name, fishing_type, time_window,
                       activity_level, predicted_activity_score, confidence_score,
                       recommended_lure_bait, recommended_rig, recommended_depth,
                       recommended_technique, target_lat, target_lon, best_access_point,
                       llm_explanation, uncertainty_notes, water_temp_estimate, air_temp_forecast,
                       wind_speed_forecast, moon_phase
                FROM bite_atlas.gold.api_fishing_predictions
                WHERE waterbody_id = {sql_str(wb_id)} AND prediction_date >= current_date()
                ORDER BY predicted_activity_score DESC
            """)
            
            if preds.empty:
                st.info(f"No predictions for {wb_name}. Weather forecast data needed.")
            else:
                st.success(f"{len(preds)} predictions for {wb_name}")
                for _, p in preds.iterrows():
                    level = p.get('activity_level', 'unknown')
                    color = '#d4edda' if level == 'high' else '#fff3cd' if level == 'moderate' else '#f8d7da'
                    with st.expander(f"🎯 {p['species_name']} — {level.upper()} (score: {p.get('predicted_activity_score',0):.2f})"):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Water Est", f"{int(p.get('water_temp_estimate',0) or 0)}°F")
                        c2.metric("Wind", f"{int(p.get('wind_speed_forecast',0) or 0)}mph")
                        c3.metric("Confidence", f"{p.get('confidence_score',0):.1f}")
                        c4.metric("Moon", p.get('moon_phase','?')[:10])
                        
                        st.markdown(f"**Lure/Bait:** {p.get('recommended_lure_bait','N/A')}")
                        st.markdown(f"**Rig:** {p.get('recommended_rig','N/A')}")
                        st.markdown(f"**Depth:** {p.get('recommended_depth','N/A')}")
                        st.markdown(f"**Technique:** {p.get('recommended_technique','N/A')}")
                        st.markdown(f"**Best Access:** {p.get('best_access_point','Nearest access')}")
                        st.markdown(f"**GPS:** {p.get('target_lat',0):.4f}, {p.get('target_lon',0):.4f}")
                        
                        if p.get('llm_explanation'):
                            st.info(f"🤖 **AI Analysis:** {p['llm_explanation']}")
                        if p.get('uncertainty_notes'):
                            st.warning(f"⚠️ {p['uncertainty_notes']}")
    else:
        st.info("Click 'Load Nearby Waterbodies' to start planning.")

# ==================== NOTES (Field Notebook) ====================
with tab_notes:
    st.markdown("### 📝 Field Notebook — Log Catches & Observations")
    
    with st.form("catch_report"):
        col1, col2 = st.columns(2)
        with col1:
            species = st.text_input("Species", placeholder="Largemouth Bass")
            waterbody = st.text_input("Waterbody", placeholder="Jordan Lake")
            catch_count = st.number_input("Catch Count", min_value=0, value=1)
            length_cm = st.number_input("Length (cm)", min_value=0.0, step=0.1, value=0.0)
        with col2:
            gear = st.selectbox("Gear Type", ["Spinning", "Baitcasting", "Fly", "Bottom Rig", "Trolling"])
            bait = st.text_input("Bait/Lure", placeholder="Texas worm")
            depth_ft = st.number_input("Depth (ft)", min_value=0, value=0)
            effort_hrs = st.number_input("Effort (hours)", min_value=0.0, step=0.5, value=2.0)
        
        notes = st.text_area("Notes", placeholder="Conditions, technique, observations...")
        photo = st.file_uploader("Photo (optional)", type=['jpg','jpeg','png'])
        is_catch = st.checkbox("This is a catch report", value=True)
        
        submitted = st.form_submit_button("Save Report")
        if submitted:
            report_id = str(uuid.uuid4())
            now_str = datetime.utcnow().isoformat()
            photo_path = None
            
            if photo:
                ext = photo.name.split('.')[-1]
                photo_filename = f"{report_id}.{ext}"
                photo_path = f"/Volumes/bite_atlas/gold/user_photos/{photo_filename}"
                try:
                    w.files.upload(photo_path, photo.getvalue(), overwrite=True)
                except Exception as e:
                    st.warning(f"Photo upload failed: {e}")
            
            gear_val = gear.lower().replace(" ", "_")
            sql = f"""
                INSERT INTO bite_atlas.gold.user_reports
                (report_id, user_id, report_datetime, waterbody_name, species_name,
                 catch_count, catch_length_cm, gear_type, bait_lure, depth_ft,
                 notes, photo_path, is_catch, effort_hours, created_at)
                VALUES (
                {sql_str(report_id)}, 'jeff', {sql_str(now_str)},
                {sql_str(waterbody)}, {sql_str(species)},
                {catch_count}, {length_cm if length_cm else 'NULL'},
                {sql_str(gear_val)}, {sql_str(bait)}, {depth_ft if depth_ft else 'NULL'},
                {sql_str(notes)}, {sql_str(photo_path)}, {str(is_catch).lower()},
                {effort_hrs if effort_hrs else 'NULL'}, {sql_str(now_str)}
                )
            """
            try:
                run_sql(sql)
                st.success(f"✅ Report saved! ID: {report_id[:8]}...")
                if photo_path:
                    st.caption(f"Photo stored: {photo_path}")
            except Exception as e:
                st.error(f"Save failed: {e}")
    
    st.divider()
    st.markdown("#### Recent Reports")
    reports = run_sql("""
        SELECT report_datetime, waterbody_name, species_name, catch_count,
               catch_length_cm, gear_type, bait_lure, depth_ft, notes, is_catch
        FROM bite_atlas.gold.user_reports
        ORDER BY report_datetime DESC LIMIT 20
    """)
    if reports.empty:
        st.info("No reports yet. Log your first catch above!")
    else:
        st.dataframe(reports, use_container_width=True, hide_index=True)

# ==================== SCIENCE ====================
with tab_science:
    st.markdown("### 🔬 Species Ecology & Source Health")
    
    sub_tab1, sub_tab2 = st.tabs(["Species Profiles", "Source Health"])
    
    with sub_tab1:
        species_df = run_sql("""
            SELECT species_name, temperature_range, habitat, lure_bait, rig_tackle,
                   retrieve_strategy, depth_strategy, techniques, forage, seasonal_behavior
            FROM bite_atlas.silver.species_ecology
            ORDER BY species_name
        """)
        if not species_df.empty:
            for _, s in species_df.iterrows():
                with st.expander(f"🐟 {s['species_name']}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Temperature:** {s.get('temperature_range','N/A')}")
                        st.markdown(f"**Habitat:** {s.get('habitat','N/A')}")
                        st.markdown(f"**Forage:** {s.get('forage','N/A')}")
                        st.markdown(f"**Season:** {s.get('seasonal_behavior','N/A')}")
                    with c2:
                        st.markdown(f"**Lure/Bait:** {s.get('lure_bait','N/A')}")
                        st.markdown(f"**Rig:** {s.get('rig_tackle','N/A')}")
                        st.markdown(f"**Depth:** {s.get('depth_strategy','N/A')}")
                        st.markdown(f"**Technique:** {s.get('techniques','N/A')}")
        else:
            st.info("No species data loaded")
    
    with sub_tab2:
        health_df = run_sql("""
            SELECT source_name, agency, health_status, hours_since_last_run,
                   last_run_records, last_error
            FROM bite_atlas.gold.source_health
            ORDER BY source_name
        """)
        if not health_df.empty:
            st.dataframe(health_df, use_container_width=True, hide_index=True)
            healthy = len(health_df[health_df['health_status'] == 'healthy'])
            total = len(health_df)
            st.metric("Healthy Sources", f"{healthy}/{total}")
        else:
            st.info("No health data loaded")