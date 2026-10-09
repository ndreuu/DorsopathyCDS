from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zlib
from dorsopathy_modules.ultrasound import UltrasoundProvider
from dorsopathy_modules.ultrasound._png import decode_png
from dorsopathy_modules.ultrasound.provider import calibration_from_dicom_regions
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

FIXTURES=Path(__file__).parent/'fixtures/ultrasound'


class UltrasoundTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name));self.provider=UltrasoundProvider()
        self.data=json.loads((FIXTURES/'source.json').read_text())

    def execute(self,data=None,frame=None):
        data=deepcopy(data or self.data)
        frame=self.store.add_bytes(frame or (FIXTURES/'frame.png').read_bytes(),'image/png')
        data['frame_artifact_id']=frame['artifact_id']
        source=self.store.add_bytes(json.dumps(data,allow_nan=False).encode(),'application/json')
        request={'schema_version':'1.0','job_id':'job-us','episode_id':'episode-test','module_id':'ultrasound',
                 'module_version':'0.1.0','input_artifacts':[source,frame],'parameters':{},'protocol_id':'synthetic-static-v1'}
        return self.provider.analyze(request,ProviderContext(self.store))

    def test_line_and_polygon_are_computed_with_original_frame_overlay(self):
        result=self.execute()
        self.assertEqual(result['status'],'complete')
        self.assertEqual([obs['value'] for obs in result['observations']],[5,12])
        self.assertEqual([obs['unit'] for obs in result['observations']],['mm','mm2'])
        self.assertTrue(all('manual_' in obs['method'] for obs in result['observations']))
        overlay=next(ref for ref in result['artifacts'] if ref['role']=='derived' and ref['media_type']=='image/png')
        width,height,rows=decode_png(self.store.read(overlay))
        self.assertEqual((width,height),(8,8))
        self.assertEqual(list(rows[1][4*3:4*3+3]),[255,255,0])
        self.assertEqual(list(rows[0][:3]),[40,40,40])
        for ref in result['artifacts']:self.store.read(ref)

    def test_anisotropic_scale_affects_each_axis_and_area(self):
        data=deepcopy(self.data);data['calibration']['mm_per_pixel_x']=2;data['calibration']['mm_per_pixel_y']=3
        result=self.execute(data)
        self.assertAlmostEqual(result['observations'][0]['value'],math.hypot(6,12))
        self.assertEqual(result['observations'][1]['value'],72)

    def test_unknown_scale_retains_preview_but_no_metric_values(self):
        data=deepcopy(self.data);data['calibration'].update(status='unknown',mm_per_pixel_x=None,mm_per_pixel_y=None,region=None,source=None,author_id=None)
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(all(obs['assessment_status']=='not_assessed' and obs['value'] is None for obs in result['observations']))
        self.assertTrue(any(ref['role']=='derived' and ref['media_type']=='image/png' for ref in result['artifacts']))

    def test_calibration_region_is_checked_for_whole_measurement(self):
        data=deepcopy(self.data);data['calibration']['region']=[0,0,3,7]
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(all(obs['value'] is None for obs in result['observations']))

    def test_self_intersection_duplicate_vertices_and_outside_points_rejected(self):
        for polygon in ([[1,1],[4,5],[4,1],[1,5]],[[1,1],[4,1],[1,1]],[[1,1],[9,1],[4,5]]):
            data=deepcopy(self.data);data['annotations']['areas'][0]['polygon']=polygon
            with self.subTest(polygon=polygon),self.assertRaises(ContractError):self.execute(data)

    def test_resize_dimension_mismatch_or_corrupt_png_rejected(self):
        data=deepcopy(self.data);data['width']=9
        with self.assertRaises(ContractError):self.execute(data)
        raw=bytearray((FIXTURES/'frame.png').read_bytes());raw[-1]^=1
        with self.assertRaises(ContractError):self.execute(frame=bytes(raw))

    def test_dicom_region_mapping_converts_cm_to_mm_and_rejects_ambiguous_region(self):
        item=SimpleNamespace(RegionSpatialFormat=1,RegionDataType=1,PhysicalUnitsXDirection=3,PhysicalUnitsYDirection=3,
                             PhysicalDeltaX=-0.01,PhysicalDeltaY=0.02,RegionLocationMinX0=0,RegionLocationMinY0=0,
                             RegionLocationMaxX1=7,RegionLocationMaxY1=7)
        cal=calibration_from_dicom_regions([item],8,8)
        self.assertAlmostEqual(cal['mm_per_pixel_x'],0.1)
        self.assertAlmostEqual(cal['mm_per_pixel_y'],0.2)
        with self.assertRaises(ContractError):calibration_from_dicom_regions([item,item],8,8)
        item.PhysicalUnitsXDirection=1
        with self.assertRaises(ContractError):calibration_from_dicom_regions([item],8,8)

    def test_png_sub_and_up_filters_decode_actual_pixel_values(self):
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',3,2,8,0,0,0,0))+chunk(b'IDAT',zlib.compress(bytes([1,10,10,10,2,1,2,3])))+chunk(b'IEND',b'')
        _,_,rows=decode_png(png)
        self.assertEqual(list(rows[0]),[10,10,10,20,20,20,30,30,30])
        self.assertEqual(list(rows[1]),[11,11,11,22,22,22,33,33,33])


if __name__=='__main__':unittest.main()
