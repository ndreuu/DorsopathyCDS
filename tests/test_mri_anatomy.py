import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

from dorsopathy_modules.mri_anatomy import MRIAnatomyProvider, synthetic_source
from dorsopathy_modules.mri_anatomy.provider import _largest_component
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore


class MRIAnatomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=ArtifactStore(Path(self.temp.name))
        self.provider=MRIAnatomyProvider()

    def analyze(self,data=None,parameters=None):
        data=synthetic_source() if data is None else data
        ref=self.store.add_bytes(json.dumps(data).encode(),"application/json")
        request={"schema_version":"1.0","job_id":"mri-synthetic-job","episode_id":"mri-synthetic-episode",
                 "module_id":"mri.anatomy","module_version":"0.1.0","input_artifacts":[ref],"parameters":parameters or {},"protocol_id":None}
        return self.provider.analyze(request,ProviderContext(self.store)),request

    def sidecar(self,result):
        for ref in result['artifacts']:
            if ref['media_type']=='application/json':
                value=json.loads(self.store.read(ref))
                if type(value)is dict and value.get('source_kind')=='mri.measurements.v1': return value
        self.fail('Missing measurement sidecar')

    def test_oblique_geometry_mask_area_and_png_evidence_use_same_grid(self):
        result,request=self.analyze(); validate_result(result,request)
        observation=result['observations'][0]
        self.assertAlmostEqual(observation['value'],12*.8*.5)
        self.assertEqual(observation['unit'],'mm2')
        self.assertEqual(observation['origin'],'synthetic_demo')
        self.assertEqual(observation['anatomy']['numbering_status'],'needs_confirmation')
        sidecar=self.sidecar(result); measurement=sidecar['measurements'][0]
        self.assertEqual(measurement['selected_component_voxel_count'],12)
        self.assertEqual(measurement['source_pointer'],'/frames/0/measurements/0')
        self.assertTrue(measurement['contour_segments'])
        for actual,expected in zip(measurement['contour_segments'][0]['points_lps_mm'][0],[11.75,22.24,31.68]):
            self.assertAlmostEqual(actual,expected)
        images=[ref for ref in result['artifacts'] if ref['media_type']=='image/png']
        self.assertEqual(len(images),3)
        for ref in images:
            payload=self.store.read(ref)
            self.assertEqual(payload[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('!II',payload[16:24]),(12,12))
            offset=8; pixels=b''
            while offset<len(payload):
                length=struct.unpack('!I',payload[offset:offset+4])[0]; kind=payload[offset+4:offset+8]
                data=payload[offset+8:offset+8+length]
                self.assertEqual(zlib.crc32(kind+data)&0xffffffff,struct.unpack('!I',payload[offset+8+length:offset+12+length])[0])
                if kind==b'IDAT': pixels+=data
                offset+=length+12
            self.assertTrue(zlib.decompress(pixels))
        self.assertTrue(all(ref['artifact_id'] in {a['artifact_id'] for a in result['artifacts']} for ref in observation['evidence_refs']))
        self.assertFalse(any(concept in observation['concept'] for concept in ('herniation','stenosis','dural','conus')))

    def test_pixel_spacing_changes_area_without_changing_pixel_count(self):
        data=synthetic_source(); data['frames'][0]['geometry']['pixel_spacing_rc_mm']=[1.6,1.0]
        result,_=self.analyze(data)
        self.assertAlmostEqual(result['observations'][0]['value'],19.2)

    def test_empty_boundary_and_declared_unsupported_masks_stay_unknown(self):
        for condition in ('empty','boundary','sampling'):
            with self.subTest(condition=condition):
                data=synthetic_source()
                if condition=='empty': data['frames'][0]['mask']=[[0]*12 for _ in range(12)]
                elif condition=='boundary': data['frames'][0]['mask'][0][5]=1; data['frames'][0]['mask'][1][5]=1; data['frames'][0]['mask'][2][5]=1; data['frames'][0]['mask'][3][5]=1
                else: data['frames'][0]['measurements'][0].update(assessment_status='not_assessed',reason='insufficient_native_sampling')
                result,_=self.analyze(data)
                self.assertEqual(result['status'],'partial'); self.assertIsNone(result['observations'][0]['value'])
                self.assertEqual(result['observations'][0]['assessment_status'],'not_assessed')

    def test_largest_component_and_native_all_pixels_methods_remain_distinct(self):
        data=synthetic_source(); data['frames'][0]['mask'][9][9]=1
        first,_=self.analyze(data); self.assertAlmostEqual(first['observations'][0]['value'],4.8)
        data['frames'][0]['measurements'][0]['method']='spinosarc-native-plane-all-mask-pixels'
        second,_=self.analyze(data); self.assertAlmostEqual(second['observations'][0]['value'],5.2)

    def test_missing_level_provenance_emits_unknown_not_zero(self):
        data=synthetic_source(); data['provenance']['unassessed_levels']=[{'level':'L1-L2','reason':'native_level_outside_coverage'}]
        result,_=self.analyze(data)
        self.assertEqual(result['status'],'partial')
        missing=next(obs for obs in result['observations'] if obs['anatomy']['level']=='L1-L2')
        self.assertIsNone(missing['value']); self.assertEqual(missing['assessment_status'],'not_assessed')
        self.assertEqual(len(missing['evidence_refs']),2)

    def test_invalid_geometry_mask_shapes_and_array_types_are_rejected(self):
        changes=[('pixel_spacing_rc_mm',[0,.5]),('row_direction_lps',[0,2,0]),('column_direction_lps',[0,.8,.6]),('shape_rc',[13,12])]
        for field,value in changes:
            with self.subTest(field=field):
                data=synthetic_source(); data['frames'][0]['geometry'][field]=value
                with self.assertRaises(ContractError): self.analyze(data)
        for pixel in (True,.5,-1):
            data=synthetic_source(); data['frames'][0]['mask'][5][5]=pixel
            with self.assertRaises(ContractError): self.analyze(data)

    def test_uploaded_model_hash_is_not_treated_as_independently_verified(self):
        data=synthetic_source(); data['model']={'id':'totalspineseg-step2','version':'r20260730','weights_sha256':'0'*64}
        with self.assertRaises(ContractError): self.analyze(data)

    def test_recorded_data_requires_exact_declared_source_identity(self):
        data=synthetic_source(); data['mode']='recorded'; data['source']={'format':'mha','artifact_id':'missing-source','sha256':'0'*64}
        with self.assertRaises(ContractError): self.analyze(data)

    def test_live_mode_delegates_and_shutdown_closes_trusted_worker(self):
        class Live:
            closed=False
            def analyze(inner,request,context):
                inner.request=request; return {'worker_sentinel':True}
            def close(inner): inner.closed=True
        live=Live(); self.provider=MRIAnatomyProvider(live)
        result,request=self.analyze(parameters={'processing_mode':'live'})
        self.assertEqual(result,{'worker_sentinel':True})
        self.assertEqual(live.request,request)
        self.provider.close(); self.assertTrue(live.closed)

    def test_platform_import_does_not_load_imaging_or_torch(self):
        process=subprocess.run([sys.executable,'-c',"import sys; from dorsopathy_modules.mri_anatomy import MRIAnatomyProvider; from dorsopathy_modules.mri_import import import_export; assert not set(('torch','SimpleITK','numpy','pydicom','nibabel')) & set(sys.modules)"],capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr.decode())

    def test_shipped_synthetic_source_request_and_result_reproduce(self):
        root=Path(__file__).parent/'fixtures'/'mri_exports'
        request=json.loads((root/'module_request.json').read_text())
        ref=self.store.add_bytes((root/'source_grid_synthetic.json').read_bytes(),'application/json')
        self.assertEqual(request['input_artifacts'],[ref])
        actual=self.provider.analyze(request,ProviderContext(self.store))
        self.assertEqual(actual,json.loads((root/'module_result.json').read_text()))

    def test_fragmented_max_size_mask_has_bounded_component_traversal(self):
        mask=[[int(row%3==0 and column%3==0) for column in range(1000)] for row in range(1000)]
        started=time.monotonic();component,count=_largest_component(mask)
        self.assertEqual(count,334*334);self.assertEqual(component,{(999,999)})
        self.assertLess(time.monotonic()-started,5,'Fragmented masks must not cause quadratic traversal')


    def test_aggregate_budgets_reject_before_reading_arrays_or_rendering(self):
        data=synthetic_source(); first=data['frames'][0]
        data['frames']=[copy.deepcopy(first) for _ in range(5)]
        for index,frame in enumerate(data['frames']):
            frame['frame_index']=index;frame['geometry']['shape_rc']=[1000,1000]
        with patch('dorsopathy_modules.mri_anatomy.provider._matrix',side_effect=AssertionError('Arrays read too early')):
            with self.assertRaisesRegex(ContractError,'aggregate pixel budget'): self.analyze(data)
        data=synthetic_source(); template=data['frames'][0]['measurements'][0]
        data['frames'][0]['measurements']=[{**template,'measurement_id':'area-'+str(i)} for i in range(65)]
        with patch('dorsopathy_modules.mri_anatomy.provider._png',side_effect=AssertionError('Rendered too early')):
            with self.assertRaisesRegex(ContractError,'measurement budget'): self.analyze(data)

    def test_boundary_is_calculated_once_and_amplification_is_bounded(self):
        from dorsopathy_modules.mri_anatomy.provider import _boundary
        data=synthetic_source();data['frames'][0]['measurements'].append({**data['frames'][0]['measurements'][0],'measurement_id':'second-area'})
        with patch('dorsopathy_modules.mri_anatomy.provider._boundary',wraps=_boundary) as boundary:
            result,_=self.analyze(data);self.assertEqual(boundary.call_count,1)
            self.assertEqual(len(result['observations']),2)
            for observation in result['observations']:self.assertAlmostEqual(observation['value'],4.8)
        with patch('dorsopathy_modules.mri_anatomy.provider._MAX_CONTOUR_SEGMENTS',20),patch('dorsopathy_modules.mri_anatomy.provider._png',side_effect=AssertionError('Rendered too early')):
            with self.assertRaisesRegex(ContractError,'aggregate evidence budget'):self.analyze(data)

if __name__=='__main__': unittest.main()
