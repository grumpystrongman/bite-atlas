"""Optional driving-time enrichment to mapped access points, never to fish pins."""
import json
import logging
import math
import os
import urllib.request

from scout import coordinates


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Routing provider redirected unexpectedly')


def enrich_routes(result, api_key=None, transport=None):
    key = api_key if api_key is not None else os.environ.get('ORS_API_KEY')
    if not key:
        result['sources'].append({'name': 'driving_routes', 'status': 'not_configured'})
        return result
    accesses = {}
    for water in result['waters']:
        for access in water['access_points']:
            point = coordinates(access)
            if point and str(access.get('closure_status', '')).lower() not in ('closed', 'temporarily closed'):
                accesses.setdefault(str(access.get('access_id') or point), access)
    bounded = list(accesses.items())[:49]
    if not bounded:
        result['sources'].append({'name': 'driving_routes', 'status': 'no_mapped_access'})
        return result
    origin = result['request']
    locations = [[origin['longitude'], origin['latitude']]]
    locations += [[coordinates(a)[1], coordinates(a)[0]] for _, a in bounded]
    body = {'locations': locations, 'sources': ['0'],
            'destinations': [str(i) for i in range(1, len(locations))], 'metrics': ['duration', 'distance']}
    try:
        if transport:
            data = transport(body)
        else:
            req = urllib.request.Request('https://api.openrouteservice.org/v2/matrix/driving-car',
                    data=json.dumps(body).encode(), headers={'Authorization': key, 'Content-Type': 'application/json'}, method='POST')
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Routing response exceeded limit')
                data = json.loads(raw)
        durations = data['durations'][0]
        if len(durations) != len(bounded):
            raise ValueError('Routing result dimensions did not match access points')
        route_by_id = {}
        for i, (aid, access) in enumerate(bounded):
            seconds = durations[i]
            valid = isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0
            route_by_id[aid] = {'drive_minutes': round(seconds / 60, 1) if valid else None,
                'within_drive_budget': seconds <= origin['max_drive_minutes'] * 60 if valid else None,
                'route_scope': 'driving_to_mapped_access_only',
                'provider': 'openrouteservice / OpenStreetMap', 'final_segment_status': 'unverified'}
            access['road_route'] = route_by_id[aid]
        for candidate in result['candidates']:
            for access in candidate['access_options']:
                aid = str(access.get('access_id') or coordinates(access))
                if aid in route_by_id:
                    access['road_route'] = route_by_id[aid]
            # Final casting-location eligibility remains unknown even when a
            # driving route reaches a nearby ramp or parking area.
        result['sources'].append({'name': 'driving_routes', 'status': 'loaded',
            'access_count': len(bounded), 'truncated': len(accesses) > len(bounded),
            'scope': 'road driving only; no off-road, walking or water navigation'})
    except Exception:
        logging.getLogger(__name__).exception('Driving-time enrichment failed')
        result['sources'].append({'name': 'driving_routes', 'status': 'unavailable'})
    return result
