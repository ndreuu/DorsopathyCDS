from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from dorsopathy_modules.nociception import NociceptionProvider
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

FIXTURES=Path(__file__).parent/'fixtures/nociception'


class NociceptionTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name));self.provider=NociceptionProvider()
        self.data=json.loads((FIXTURES/'source.json').read_text())

    def execute(self,data=None):
        data=deepcopy(data or self.data);source=self.store.add_bytes(json.dumps(data,allow_nan=False).encode(),'application/json')
        request={'schema_version':'1.0','job_id':'job-index','episode_id':'episode-test','module_id':'nociception','module_version':'0.1.0',
                 'input_artifacts':[source],'parameters':{},'protocol_id':'synthetic-static-v1'}
        return self.provider.analyze(request,ProviderContext(self.store))

    def test_canonical_mean_and_events_are_preserved_without_vendor_claim(self):
        result=self.execute();self.assertEqual(result['status'],'complete')
        self.assertEqual(result['observations'][0]['value'],40)
        self.assertEqual(result['observations'][0]['unit'],'1')
        self.assertEqual(result['observations'][0]['origin'],'synthetic_demo')
        summary=next(json.loads(self.store.read(ref)) for ref in result['artifacts'] if ref['media_type']=='application/json' and json.loads(self.store.read(ref)).get('source_kind')=='nociception.measurements.v1')
        self.assertEqual(summary['selected_row_indices'],[0,1,2]);self.assertEqual(summary['comparison_metadata']['event_id'],'baseline')
        self.assertEqual(summary['device']['index_field'],'demo_index')
        self.assertTrue(any(ref['media_type']=='image/png' for ref in result['artifacts']))

    def test_mean_uses_exact_values_and_half_open_window(self):
        data=deepcopy(self.data)
        for row,value in zip(data['rows'],[20.12345,40.23456,60.34567]):row['value']=value
        self.assertAlmostEqual(self.execute(data)['observations'][0]['value'],40.23456)
        data=deepcopy(self.data);data['selection']['end_s']=2
        self.assertEqual(self.execute(data)['observations'][0]['value'],30)

    def test_missing_or_bad_quality_is_unknown_not_zero_index(self):
        for key,value in (('value',None),('quality','unreliable'),('quality','unknown')):
            data=deepcopy(self.data);data['rows'][1][key]=value
            result=self.execute(data);self.assertEqual(result['status'],'partial')
            self.assertIsNone(result['observations'][0]['value'])

    def test_unknown_clock_preserves_relative_mean_but_cannot_claim_usable_timing(self):
        data=deepcopy(self.data);data['acquired_at']=None;data['clock_domain']=None
        result=self.execute(data)
        self.assertEqual(result['observations'][0]['value'],40)
        self.assertEqual(result['observations'][0]['quality'],'unknown')
        self.assertIsNone(result['observations'][0]['acquired_at'])

    def test_unknown_units_sampling_or_device_window_blocks_selected_value(self):
        changes=[('unit',None),('sampling_period_s',None)]
        for key,value in changes:
            data=deepcopy(self.data);data[key]=value
            self.assertIsNone(self.execute(data)['observations'][0]['value'])
        data=deepcopy(self.data);data['device']['averaging_window_s']=None
        self.assertIsNone(self.execute(data)['observations'][0]['value'])

    def test_gap_missing_time_and_empty_window_are_explicit(self):
        data=deepcopy(self.data);data['rows'][2]['time_s']=3;data['selection']['end_s']=4
        self.assertIsNone(self.execute(data)['observations'][0]['value'])
        data=deepcopy(self.data);data['rows'][1]['time_s']=None
        self.assertIsNone(self.execute(data)['observations'][0]['value'])
        data=deepcopy(self.data);data['selection'].update(start_s=20,end_s=22)
        self.assertIsNone(self.execute(data)['observations'][0]['value'])

    def test_missing_first_sample_is_incomplete_window_not_mean_of_remaining_rows(self):
        data=deepcopy(self.data);data['rows']=data['rows'][1:]
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['observations'][0]['value'])
        self.assertEqual(result['observations'][0]['quality'],'unreliable')

    def test_invalid_scale_duplicate_timestamps_and_undeclared_event_rejected(self):
        changes=[('value',101),('value',-1),('time_s',0),('events',['unknown-event'])]
        for key,value in changes:
            data=deepcopy(self.data);data['rows'][1][key]=value
            with self.assertRaises(ContractError):self.execute(data)

    def test_vendor_format_and_capture_are_not_claimed_and_svg_labels_escape(self):
        data=deepcopy(self.data);data['source_kind']='ani-monitor-v2-export'
        with self.assertRaises(ContractError):self.execute(data)
        data=deepcopy(self.data);data['acquisition_mode']='device'
        with self.assertRaises(ContractError):self.execute(data)
        data=deepcopy(self.data);data['events'][0]['label']='<script>alert(1)</script>'
        result=self.execute(data);svg=next(ref for ref in result['artifacts'] if ref['media_type']=='image/svg+xml')
        self.assertNotIn(b'<script>',self.store.read(svg));self.assertIn(b'&lt;script&gt;',self.store.read(svg))


if __name__=='__main__':unittest.main()
