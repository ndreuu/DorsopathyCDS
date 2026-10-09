from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from dorsopathy_modules.thermography import ThermographyProvider
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

FIXTURES=Path(__file__).parent/'fixtures/thermography'


class ThermographyTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name));self.provider=ThermographyProvider()
        self.data=json.loads((FIXTURES/'source.json').read_text())

    def execute(self,data=None):
        data=deepcopy(data or self.data)
        source=self.store.add_bytes(json.dumps(data,allow_nan=False).encode(),'application/json')
        request={'schema_version':'1.0','job_id':'job-thermal','episode_id':'episode-test','module_id':'thermography',
                 'module_version':'0.1.0','input_artifacts':[source],'parameters':{},'protocol_id':'synthetic-static-v1'}
        return self.provider.analyze(request,ProviderContext(self.store))

    def test_roi_axis_order_means_and_signed_delta_are_computed(self):
        result=self.execute()
        self.assertEqual(result['status'],'complete')
        self.assertEqual([obs['value'] for obs in result['observations']],[32,34,-2])
        self.assertEqual([obs['unit'] for obs in result['observations']],['Cel','Cel','K'])
        self.assertEqual([obs['anatomy']['side'] for obs in result['observations']],['left','right','bilateral'])
        svg=next(ref for ref in result['artifacts'] if ref['media_type']=='image/svg+xml')
        self.assertIn(b'Cel',self.store.read(svg))
        self.assertTrue(any(ref['media_type']=='image/png' for ref in result['artifacts']))

    def test_changing_real_matrix_values_changes_computed_means(self):
        data=deepcopy(self.data);data['temperature_matrix'][1][0]=36
        result=self.execute(data)
        self.assertEqual([obs['value'] for obs in result['observations']],[34,34,0])

    def test_missing_calibration_is_unknown_not_normal_temperature(self):
        data=deepcopy(self.data);data['calibration'].update(status='unknown',source=None,author_id=None)
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(all(obs['assessment_status']=='not_assessed' and obs['value'] is None for obs in result['observations']))

    def test_missing_pixels_and_outside_roi_preserve_other_measurements(self):
        for value in (None,'outside'):
            data=deepcopy(self.data)
            if value is None:data['temperature_matrix'][0][0]=None
            else:data['rois'][0]['pixels'][0]=[99,99]
            result=self.execute(data)
            self.assertEqual(result['status'],'partial')
            self.assertIsNone(result['observations'][0]['value'])
            self.assertEqual(result['observations'][1]['value'],34)
            self.assertIsNone(result['observations'][2]['value'])

    def test_unknown_conditions_or_incompatible_pair_cannot_create_delta(self):
        for mode in ('conditions','pair'):
            data=deepcopy(self.data)
            if mode=='conditions':data['conditions']['emissivity']=None
            else:data['pairs'][0]['compatibility']='incompatible'
            result=self.execute(data)
            self.assertEqual(result['status'],'partial')
            self.assertIsNone(result['observations'][2]['value'])

    def test_ragged_wrong_units_and_duplicate_roi_coordinates_rejected(self):
        changes=[('unit','K'),('temperature_matrix',[[32],[33,34]])]
        for field,value in changes:
            data=deepcopy(self.data);data[field]=value
            with self.assertRaises(ContractError):self.execute(data)
        data=deepcopy(self.data);data['rois'][0]['pixels']=[[0,0],[0,0]]
        with self.assertRaises(ContractError):self.execute(data)

    def test_false_color_jpeg_is_not_a_temperature_profile(self):
        source=self.store.add_bytes(b'not radiometric JPEG','image/jpeg')
        request={'schema_version':'1.0','job_id':'job-jpeg','episode_id':'episode-test','module_id':'thermography',
                 'module_version':'0.1.0','input_artifacts':[source],'parameters':{},'protocol_id':None}
        with self.assertRaises(ContractError):self.provider.analyze(request,ProviderContext(self.store))


if __name__=='__main__':unittest.main()
