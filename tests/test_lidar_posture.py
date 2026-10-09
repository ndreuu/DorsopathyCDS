from copy import deepcopy
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from dorsopathy_modules.lidar_posture import LidarPostureProvider
from dorsopathy_modules.lidar_posture.provider import import_geometry
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

FIXTURES=Path(__file__).parent/'fixtures/lidar'


class LidarTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name));self.provider=LidarPostureProvider()
        self.data=json.loads((FIXTURES/'source.json').read_text())

    def execute(self,data=None,geometry=None):
        data=deepcopy(data or self.data)
        geometry=self.store.add_bytes(geometry or (FIXTURES/('triangle.'+data['format'])).read_bytes(),'text/plain')
        data['geometry_artifact_id']=geometry['artifact_id']
        source=self.store.add_bytes(json.dumps(data,allow_nan=False).encode(),'application/json')
        request={'schema_version':'1.0','job_id':'job-lidar','episode_id':'episode-test','module_id':self.provider.spec.module_id,
                 'module_version':'0.1.0','input_artifacts':[source,geometry],'parameters':{},'protocol_id':'synthetic-static-v1'}
        return self.provider.analyze(request,ProviderContext(self.store))

    def test_triangle_is_measured_and_evidence_geometry_annotations_and_png_resolve(self):
        result=self.execute()
        self.assertEqual(result['status'],'complete')
        self.assertEqual([obs['value'] for obs in result['observations']],[500,90])
        self.assertEqual([obs['unit'] for obs in result['observations']],['mm','deg'])
        self.assertTrue(all(obs['origin']=='synthetic_demo' for obs in result['observations']))
        for ref in result['artifacts']:
            self.store.read(ref)
        image=next(ref for ref in result['artifacts'] if ref['media_type']=='image/png')
        self.assertEqual(struct.unpack('>II',self.store.read(image)[16:24]),(256,256))
        summary=next(json.loads(self.store.read(ref)) for ref in result['artifacts'] if ref['media_type']=='application/json' and json.loads(self.store.read(ref)).get('source_kind')=='lidar.measurements.v1')
        self.assertEqual(summary['comparison_metadata']['calibration_status'],'verified')
        self.assertTrue(summary['comparison_metadata']['calibration_evidence_refs'])
        self.assertEqual(summary['comparison_metadata']['transform_refs'],[])

    def test_obj_and_declared_scale_use_same_arithmetic(self):
        data=deepcopy(self.data);data['format']='obj';data['calibration']['mm_per_unit']=0.001
        result=self.execute(data)
        self.assertAlmostEqual(result['observations'][0]['value'],0.5)
        self.assertAlmostEqual(result['observations'][1]['value'],90)

    def test_unknown_scale_is_not_zero_but_dimensionless_angle_remains_known(self):
        data=deepcopy(self.data);data['calibration'].update(status='unknown',mm_per_unit=None,source=None,author_id=None)
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['observations'][0]['assessment_status'],'not_assessed')
        self.assertIsNone(result['observations'][0]['value'])
        self.assertEqual(result['observations'][1]['value'],90)

    def test_missing_landmark_and_partial_coverage_are_explicit(self):
        data=deepcopy(self.data);data['annotations']['measurements'][0]['landmarks'][1]='missing';data['coverage']='partial'
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['observations'][0]['value'])
        self.assertEqual(result['observations'][1]['quality'],'unreliable')

    def test_controlled_vertex_perturbation_changes_distance(self):
        geometry=(FIXTURES/'triangle.ply').read_bytes().replace(b'300 400 0',b'300 401 0')
        result=self.execute(geometry=geometry)
        self.assertAlmostEqual(result['observations'][0]['value'],math.hypot(300,401))
        self.assertLess(result['observations'][0]['value']-500,1)

    def test_binary_nan_invalid_faces_and_empty_geometry_are_rejected(self):
        for raw in (b'ply\nformat binary_little_endian 1.0\n',b'v nan 0 0\n',b'v 0 0 0\nf 1 2 3\n',b'# no vertices\n'):
            with self.subTest(raw=raw),self.assertRaises(ContractError):
                import_geometry(io.BytesIO(raw),'ply' if raw.startswith(b'ply') else 'obj')

    def test_capture_or_mismatched_origin_cannot_be_accepted(self):
        for field,value in (('acquisition_mode','device'),('origin','automatically_measured')):
            data=deepcopy(self.data);data[field]=value
            with self.assertRaises(ContractError):self.execute(data)


if __name__=='__main__':unittest.main()
