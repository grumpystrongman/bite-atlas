import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/bite-atlas'))
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scout import ScoutRequest, rank_scout, scout, distance_km
from scout_routes import enrich_routes
import api

WHEN = '2026-10-17T14:00:00-04:00'

def request(**kwargs):
    return ScoutRequest(latitude=36, longitude=-79, species=['Bass'], target_time=WHEN, **kwargs)

def data():
    return {'waters': [{'waterbody_id':'w1','canonical_name':'Test Lake','lat':36,'lon':-79}],
            'attractors':[{'waterbody_name':'Test Lake','lat':36.001,'lon':-79,'attractor_type':'Brush pile','full_pool_depth_ft':12,'status':'active'}],
            'ecology':[{'species_name':'Bass','lure_bait':'General lure guidance'}],
            'access':[{'access_id':'a1','waterbody_id':'w1','latitude':36.003,'longitude':-79,'access_name':'Mapped ramp','closure_status':'open'}]}

class RankingTests(unittest.TestCase):
    def test_no_centroid_or_pier_fabricated_as_spot(self):
        d=data();d['attractors']=[]
        result=rank_scout(request(mode='any'),d,[])
        self.assertEqual(result['candidates'],[])
        self.assertEqual(len(result['waters']),1)
        self.assertTrue(any(f['properties'].get('centroid_only') for f in result['map']['features']))

    def test_unknown_occurrence_never_high_confidence(self):
        c=rank_scout(request(mode='any'),data(),[])['candidates'][0]
        self.assertEqual(c['confidence'],'low')
        self.assertEqual(c['species_evidence'],[])
        self.assertIsNone(c['within_drive_budget'])
        self.assertEqual(c['depth_reference'],'full_pool_not_current_depth')
        self.assertIn('not probability',c['score_meaning'])
        self.assertEqual(c['general_species_guidance']['lure_bait'],'General lure guidance')

    def test_bank_requires_reachability_not_nearby_pier(self):
        self.assertEqual(rank_scout(request(mode='bank'),data(),[])['candidates'],[])
        d=data();d['habitat']=[{'candidate_id':'shore1','waterbody_id':'w1','latitude':36.002,'longitude':-79,'feature_type':'bank_point','source_data':'{"bank_reachable":true}'}]
        result=rank_scout(request(mode='bank'),d,[])
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(result['candidates'][0]['candidate_id'],'shore1')
        self.assertEqual(result['candidates'][0]['confidence'],'low')

    def test_occurrence_requires_source_and_nonfuture_date(self):
        d=data();d['occurrence']=[{'waterbody_id':'w1','species_name':'Bass','source_url':'https://example.org/survey','observed_at':'2027-01-01T00:00:00Z'}]
        self.assertEqual(rank_scout(request(mode='any'),d,[])['waters'][0]['species'][0]['occurrence_status'],'unknown')
        d['occurrence'][0]['observed_at']='2026-01-01T00:00:00Z'
        self.assertEqual(rank_scout(request(mode='any'),d,[])['waters'][0]['species'][0]['occurrence_status'],'documented')

    def test_invalid_coordinates_and_ambiguous_names_excluded(self):
        d=data();d['waters'].append({'waterbody_id':'w2','canonical_name':'Test Lake','lat':36.01,'lon':-79})
        self.assertEqual(rank_scout(request(mode='any'),d,[])['candidates'],[])
        d=data();d['attractors'][0]['lat']='NaN'
        self.assertEqual(rank_scout(request(mode='any'),d,[])['candidates'],[])
        self.assertAlmostEqual(distance_km((36,-79),(36,-79)),0)

    def test_source_gap_and_query_parameters(self):
        seen=[]
        def query(sql,parameters=None):
            seen.append((sql,parameters))
            if 'api_nearby_waters' in sql:return data()['waters']
            if 'api_scout_species_occurrence' in sql:raise RuntimeError('Missing view')
            if 'api_fish_attractors' in sql:return data()['attractors']
            return []
        malicious="Bass'); DROP TABLE x; --"
        req=ScoutRequest(latitude=36,longitude=-79,species=[malicious],target_time=WHEN,mode='any')
        with self.assertLogs('scout',level='ERROR'):result=scout(req,query)
        self.assertIn({'name':'occurrence','status':'unavailable','row_count':None},result['sources'])
        self.assertTrue(any(malicious in (p or {}).values() for _,p in seen))
        self.assertTrue(all(malicious not in sql for sql,_ in seen))

    def test_bad_requests(self):
        for values in [{'latitude':float('nan')},{'target_time':'2026-10-17T14:00:00'},{'species':[]},{'species':['a']*6},{'radius_km':0}]:
            args=dict(latitude=36,longitude=-79,species=['Bass'],target_time=WHEN);args.update(values)
            with self.assertRaises(ValidationError):ScoutRequest(**args)

class RoutingTests(unittest.TestCase):
    def test_unconfigured_route_does_not_estimate_from_distance(self):
        r=rank_scout(request(mode='any'),data(),[])
        enrich_routes(r,api_key='')
        self.assertIsNone(r['candidates'][0]['drive_minutes'])
        self.assertEqual(r['sources'][-1]['status'],'not_configured')

    def test_driving_window_applies_to_access_not_casting_point(self):
        r=rank_scout(request(mode='any'),data(),[])
        enrich_routes(r,api_key='fixture',transport=lambda body:{'durations':[[1200]]})
        road=r['candidates'][0]['access_options'][0]['road_route']
        self.assertEqual(road['drive_minutes'],20)
        self.assertTrue(road['within_drive_budget'])
        self.assertIsNone(r['candidates'][0]['within_drive_budget'])
        self.assertEqual(road['final_segment_status'],'unverified')

    def test_unreachable_route_remains_unknown(self):
        r=rank_scout(request(mode='any'),data(),[])
        enrich_routes(r,api_key='fixture',transport=lambda body:{'durations':[[None]]})
        self.assertIsNone(r['waters'][0]['access_points'][0]['road_route']['within_drive_budget'])

class ApiTests(unittest.TestCase):
    def setUp(self):self.client=TestClient(api.app)

    def test_home_and_static_are_served_without_databricks_credentials(self):
        self.assertIn('Scout',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/scout.js').status_code,200)
        self.assertEqual(self.client.get('/legacy').status_code,200)
        self.assertIn('/api/scout',self.client.get('/api/openapi.json').json()['paths'])

    def test_scout_validation_and_empty_real_results(self):
        with patch.object(api,'run_sql',return_value=[]),patch.dict('os.environ',{'ORS_API_KEY':''}):
            r=self.client.post('/api/scout',json=request().model_dump(mode='json'))
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['candidates'],[])
        self.assertEqual(self.client.post('/api/scout',json={'latitude':36}).status_code,422)

    def test_failed_sql_is_not_empty_success(self):
        failed=NS(statement_id='stmt',status=NS(state='FAILED',error=NS(message='Permission denied')))
        client=NS(statement_execution=NS(execute_statement=lambda **kwargs:failed))
        with patch.object(api,'ensure_warehouse'),patch.object(api,'get_client',return_value=client):
            with self.assertRaisesRegex(RuntimeError,'Permission denied'):api.run_sql('SELECT x')
        with patch.object(api,'run_sql',side_effect=RuntimeError('sensitive database detail')):
            with self.assertLogs('api',level='ERROR'):r=self.client.get('/api/species')
        self.assertEqual(r.status_code,503)
        self.assertNotIn('sensitive',r.text)
        self.assertIn('diagnostic_id',r.json())

    def test_sql_chunks_and_parameters(self):
        result=NS(data_array=[['1']],next_chunk_index=1)
        resp=NS(statement_id='stmt',status=NS(state='SUCCEEDED'),result=result,manifest=NS(schema=NS(columns=[NS(name='value')])))
        captured={}
        def execute(**kwargs):captured.update(kwargs);return resp
        client=NS(statement_execution=NS(execute_statement=execute,get_statement_result_chunk_n=lambda sid,idx:NS(data_array=[['2']],next_chunk_index=None)))
        with patch.object(api,'ensure_warehouse'),patch.object(api,'get_client',return_value=client):rows=api.run_sql('SELECT :name',parameters={'name':"O'Brien"})
        self.assertEqual(rows,[{'value':1},{'value':2}])
        self.assertEqual(captured['parameters'][0].value,"O'Brien")

if __name__=='__main__':unittest.main()
