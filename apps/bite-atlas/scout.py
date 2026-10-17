"""Evidence-aware fishing reconnaissance. Scores are priorities, not catch odds.

No shoreline/centroid is invented as a fishing spot. This module ranks actual
mapped structures and supplied habitat candidates and exposes every missing gate.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import math
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


class ScoutRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    species: list[str] = Field(min_length=1, max_length=5)
    target_time: datetime
    radius_km: float = Field(default=40, gt=0, le=150, allow_inf_nan=False)
    max_drive_minutes: int = Field(default=30, ge=1, le=180)
    mode: Literal['bank', 'kayak', 'boat', 'any'] = 'bank'
    waterbody_limit: int = Field(default=12, ge=1, le=20)

    @field_validator('species')
    @classmethod
    def species_names(cls, values):
        names = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if not names or any(len(n) > 100 for n in names):
            raise ValueError('Select one to five named species, each at most 100 characters.')
        return names

    @field_validator('target_time')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('target_time must include a UTC offset.')
        return value


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def coordinates(row):
    lat = number(row.get('latitude', row.get('lat')))
    lon = number(row.get('longitude', row.get('lon')))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def _proof(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _observed_by(value, when):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)  # occurrence contract uses UTC
        return dt <= when
    except (ValueError, TypeError):
        return False


def rank_scout(request, datasets, statuses):
    """Pure ranking function; testable independently of Databricks and map vendors."""
    waters = datasets.get('waters', [])
    origin = request.latitude, request.longitude
    profiles = {p.get('species_name'): p for p in datasets.get('ecology', [])}
    occurrences = datasets.get('occurrence', [])
    accesses = datasets.get('access', [])
    spots, features, water_results = [], [], []
    by_name = {}
    for w in waters:
        by_name.setdefault(str(w.get('canonical_name', '')).casefold(), []).append(w)
    candidates = []
    for row in datasets.get('habitat', []):
        proof = _proof(row.get('source_data'))
        candidates.append({**row, 'candidate_id': row.get('candidate_id'),
                           'basis': 'inferred_habitat', 'source_table': 'bite_atlas.gold.habitat_candidates',
                           'provenance': proof, 'depth_reference': 'unverified',
                           'label': row.get('feature_type') or 'Habitat candidate'})
    for row in datasets.get('attractors', []):
        matches = by_name.get(str(row.get('waterbody_name', '')).casefold(), [])
        if len(matches) != 1 or str(row.get('status', '')).casefold() in ('removed', 'inactive', 'closed'):
            continue
        candidates.append({**row, 'waterbody_id': matches[0]['waterbody_id'],
                           'candidate_id': f"attractor:{matches[0]['waterbody_id']}:{row.get('lat')}:{row.get('lon')}",
                           'basis': 'mapped_structure', 'source_table': 'bite_atlas.gold.api_fish_attractors',
                           'depth_ft': row.get('full_pool_depth_ft'), 'depth_reference': 'full_pool_not_current_depth',
                           'provenance': {}, 'label': row.get('attractor_type') or 'Mapped fish attractor'})
    for water in waters:
        wid = water['waterbody_id']
        pos = coordinates(water)
        if not pos:
            continue
        nearby_access = [a for a in accesses if a.get('waterbody_id') == wid and coordinates(a)]
        # An agency access record is a mapped location, not a verified route or
        # permission to use an arbitrary intervening shoreline/private parcel.
        for a in nearby_access:
            features.append(_feature(coordinates(a), {**a, 'kind': 'access', 'legal_status': 'requires_verification'}))
        features.append(_feature(pos, {'kind': 'water', 'waterbody_id': wid,
                                       'name': water.get('canonical_name'), 'centroid_only': True}))
        water_info = {'waterbody_id': wid, 'name': water.get('canonical_name'),
                      'distance_km': round(distance_km(origin, pos), 2),
                      'access_points': nearby_access, 'species': [], 'candidate_count': 0}
        for species in request.species:
            records = [o for o in occurrences if o.get('waterbody_id') == wid
                       and o.get('species_name') == species and o.get('source_url')
                       and _observed_by(o.get('observed_at'), request.target_time)]
            water_info['species'].append({'species_name': species,
                'occurrence_status': 'documented' if records else 'unknown',
                'evidence': records, 'general_ecology': profiles.get(species)})
        for candidate in candidates:
            if candidate.get('waterbody_id') != wid:
                continue
            point = coordinates(candidate)
            if not point or distance_km(origin, point) > request.radius_km:
                continue
            # Bank access needs an actual shore/walkability link. A submerged
            # fish attractor by itself is never called a bank fishing position.
            kind = str(candidate.get('feature_type', '')).lower()
            bank_reachable = candidate['basis'] == 'inferred_habitat' and candidate['provenance'].get('bank_reachable') is True
            if request.mode == 'bank' and not bank_reachable:
                continue
            distance = distance_km(origin, point)
            for species_info in water_info['species']:
                documented = species_info['occurrence_status'] == 'documented'
                source_measured = candidate['basis'] == 'mapped_structure'
                provenance = candidate['provenance']
                verified_water_link = provenance.get('waterbody_geometry_verified') is True
                # No source says how likely a fish is to bite. Numeric scores
                # express explainable evidence priorities, not calibrated odds.
                components = {'structure': 30 if source_measured else 12,
                              'species_evidence': 30 if documented else 0,
                              'proximity': round(10 * max(0, 1-distance/request.radius_km), 1),
                              'geometry_validation': 10 if verified_water_link else 0}
                gaps = ['Driving time not computed; 30-minute eligibility is unverified.',
                        'Legal access and the final bank/water route need verification.',
                        'Current conditions are not validated for this casting location.']
                if not documented:
                    gaps.append('No dated species occurrence evidence is available for this waterbody.')
                if not verified_water_link:
                    gaps.append('Candidate-to-waterbody geometry association is not verified.')
                if number(candidate.get('depth_ft')) is None:
                    gaps.append('Measured depth is unavailable; no hole or drop-off is asserted.')
                elif candidate['depth_reference'] != 'current_survey':
                    gaps.append('Depth is source/reference depth, not a verified current sounding.')
                profile = species_info['general_ecology'] or {}
                access_options = []
                for a in nearby_access:
                    if str(a.get('closure_status', '')).lower() in ('closed', 'temporarily closed'):
                        continue
                    access_options.append({**a, 'distance_to_candidate_km': round(distance_km(coordinates(a), point), 2),
                                           'connection_status': 'not_verified'})
                access_options.sort(key=lambda a: a['distance_to_candidate_km'])
                spot = {'candidate_id': candidate['candidate_id'], 'waterbody_id': wid,
                        'waterbody_name': water.get('canonical_name'), 'species_name': species_info['species_name'],
                        'name': candidate['label'], 'feature_type': kind or candidate.get('attractor_type'),
                        'latitude': point[0], 'longitude': point[1], 'basis': candidate['basis'],
                        'ranking_score': round(sum(components.values()), 1), 'score_components': components,
                        'confidence': 'moderate' if documented and source_measured and verified_water_link else 'low',
                        'score_meaning': 'Uncalibrated evidence priority, not probability of catching a fish.',
                        'depth_ft': number(candidate.get('depth_ft')), 'depth_reference': candidate['depth_reference'],
                        'source_table': candidate['source_table'], 'provenance': provenance,
                        'species_evidence': species_info['evidence'], 'distance_km': round(distance, 2),
                        'drive_minutes': None, 'within_drive_budget': None,
                        'access_options': access_options[:3], 'bank_reachability': 'documented_in_candidate' if bank_reachable else 'unknown',
                        'general_species_guidance': {k: profile.get(k) for k in ['lure_bait', 'rig_tackle', 'techniques', 'depth_strategy']},
                        'guidance_basis': 'General species ecology; not a condition-specific prescription.',
                        'gaps': gaps, 'target_time': request.target_time.isoformat()}
                spots.append(spot)
                water_info['candidate_count'] += 1
                features.append(_feature(point, {'kind': 'candidate', 'candidate_id': candidate['candidate_id'],
                    'species_name': species_info['species_name'], 'name': candidate['label'], 'confidence': spot['confidence']}))
        water_results.append(water_info)
    spots.sort(key=lambda s: (-s['ranking_score'], s['distance_km'], str(s['candidate_id']), s['species_name']))
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'target_time': request.target_time.isoformat(),
            'request': request.model_dump(mode='json'), 'waters': water_results, 'candidates': spots[:100],
            'candidate_total': len(spots), 'candidate_limit': 100,
            'map': {'type': 'FeatureCollection', 'features': features}, 'sources': statuses,
            'limitations': ['North Carolina Gold data is the current scope; provider coverage is incomplete.',
                'Water centroids are discovery markers, never recommended casting positions.',
                'Land terrain is not bathymetry. No depth is inferred from aerial imagery.',
                'No automatic road, off-road, bank or navigable-water routing is configured.',
                'A mapped access point does not establish legal access to a candidate.',
                'No calibrated catch probabilities or live fish detections are provided.']}


def _feature(point, props):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [point[1], point[0]]}, 'properties': props}


def scout(request: ScoutRequest, query: Callable):
    # All interpolated geometry values are bounded finite floats validated by
    # Pydantic. Text values use Statement Execution named parameters.
    lat, lon, radius = request.latitude, request.longitude, request.radius_km
    dist = f"6371*2*ASIN(SQRT(LEAST(1D,POWER(SIN(RADIANS(lat-({lat}))/2),2)+COS(RADIANS({lat}))*COS(RADIANS(lat))*POWER(SIN(RADIANS(lon-({lon}))/2),2))))"
    waters = query(f"SELECT *, {dist} AS distance_km FROM bite_atlas.gold.api_nearby_waters WHERE lat IS NOT NULL AND lon IS NOT NULL AND {dist} <= {radius} ORDER BY distance_km LIMIT {request.waterbody_limit}")
    datasets, statuses = {'waters': waters}, [{'name': 'waters', 'status': 'loaded', 'row_count': len(waters), 'limit': request.waterbody_limit, 'possibly_truncated': len(waters) == request.waterbody_limit}]
    if not waters:
        return rank_scout(request, datasets, statuses)
    ids = {f'w{i}': row['waterbody_id'] for i, row in enumerate(waters)}
    in_ids = ','.join(':'+k for k in ids)
    names = {f'n{i}': row.get('canonical_name', '') for i, row in enumerate(waters)}
    in_names = ','.join(':'+k for k in names)
    species = {f's{i}': value for i, value in enumerate(request.species)}
    in_species = ','.join(':'+k for k in species)
    jobs = {
        'access': (f'SELECT * FROM bite_atlas.gold.api_access_points WHERE waterbody_id IN ({in_ids}) LIMIT 500', ids),
        'ecology': (f'SELECT * FROM bite_atlas.gold.api_species_ecology WHERE species_name IN ({in_species})', species),
        'habitat': (f'SELECT * FROM bite_atlas.gold.habitat_candidates WHERE waterbody_id IN ({in_ids}) LIMIT 500', ids),
        'attractors': (f'SELECT * FROM bite_atlas.gold.api_fish_attractors WHERE waterbody_name IN ({in_names}) LIMIT 500', names),
        'occurrence': (f'SELECT * FROM bite_atlas.gold.api_scout_species_occurrence WHERE waterbody_id IN ({in_ids}) AND species_name IN ({in_species}) LIMIT 1000', {**ids, **species}),
    }
    def load(item):
        name, (sql, params) = item
        try:
            result = query(sql, parameters=params)
            return name, result, {'name': name, 'status': 'loaded', 'row_count': len(result), 'bounded': name != 'ecology', 'possibly_truncated': len(result) >= (1000 if name == 'occurrence' else 500) if name != 'ecology' else False}
        except Exception:
            log.exception('Optional Scout source unavailable: %s', name)
            return name, [], {'name': name, 'status': 'unavailable', 'row_count': None}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, result, status in pool.map(load, jobs.items()):
            datasets[name] = result
            statuses.append(status)
    return rank_scout(request, datasets, statuses)
