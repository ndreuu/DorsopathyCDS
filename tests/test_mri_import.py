import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dorsopathy_modules.mri_anatomy import analyze_source, synthetic_source
from dorsopathy_modules.mri_import import bundle_source_arrays, import_export, normalize_parent_export
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_io import read_worker_output,write_worker_request
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_workers.mri.worker import main as worker_main
from dorsopathy_workers.mri.worker import _infer


PARENT=Path('/Users/andrew/.codex/worktrees/7179/med diploma')
HAS_IMAGING=all(importlib.util.find_spec(name) is not None for name in ('numpy','SimpleITK','pydicom'))


class MRIImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=ArtifactStore(self.root/'store');self.context=ProviderContext(self.store)
        self.request={'schema_version':'1.0','job_id':'import-test','episode_id':'import-episode','module_id':'mri.anatomy','module_version':'0.1.0','input_artifacts':[],'parameters':{},'protocol_id':None}

    def test_offline_synthetic_export_measures_mask_without_inference(self):
        export=self.root/'synthetic.json';export.write_text(json.dumps(synthetic_source()))
        result=import_export(export,self.context,self.request)
        self.assertAlmostEqual(result['observations'][0]['value'],4.8)
        self.assertEqual(result['observations'][0]['origin'],'synthetic_demo')

    def test_externalized_plane_arrays_remain_exact_upload_evidence(self):
        data=synthetic_source();bundled,refs=bundle_source_arrays(data,self.context)
        self.assertIsNotNone(data['frames'][0]['image'])
        self.assertIsNone(bundled['frames'][0]['image']);self.assertIsNone(bundled['frames'][0]['mask'])
        source=self.store.add_bytes(json.dumps(bundled).encode(),'application/json','source')
        request={**self.request,'input_artifacts':[source,*refs]}
        from dorsopathy_modules.mri_anatomy import MRIAnatomyProvider
        result=MRIAnatomyProvider().analyze(request,self.context)
        self.assertAlmostEqual(result['observations'][0]['value'],4.8)
        self.assertTrue({ref['artifact_id'] for ref in refs}<={ref['artifact_id'] for ref in result['artifacts']})
        for key in ('image','mask'):
            identity=bundled['frames'][0][key+'_artifact_id'];ref=next(ref for ref in refs if ref['artifact_id']==identity)
            self.assertEqual(json.loads(self.store.read(ref)),data['frames'][0][key])

    def test_export_without_linked_sources_and_symlinks_is_rejected(self):
        export=self.root/'unlinked.json';export.write_text(json.dumps({'spine.canal.cross_sectional_area':75}))
        with self.assertRaises(ContractError): import_export(export,self.context,self.request)
        link=self.root/'link.json';link.symlink_to(export)
        with self.assertRaises(ContractError): import_export(link,self.context,self.request)

    def test_worker_failure_publishes_safe_failed_wire_result(self):
        exchange=self.root/'exchange';exchange.mkdir();(exchange/'inputs').mkdir()
        ref=self.store.add_bytes(b'private-source-data','application/x-metaimage')
        shutil.copyfile(self.store.root/ref['storage_key'],exchange/'inputs'/ref['sha256'])
        request={**self.request,'input_artifacts':[ref],'parameters':{'processing_mode':'live'}}
        write_worker_request(exchange,request,[{'artifact_id':ref['artifact_id'],'relative_path':'inputs/'+ref['sha256']}])
        with patch('dorsopathy_workers.mri.worker.configure_parent'),patch('dorsopathy_workers.mri.worker.fresh_anatomy',side_effect=ContractError('private-source-data')):
            self.assertEqual(worker_main(['--exchange-dir',str(exchange),'--parent-root',str(self.root),'--tss-python',str(self.root/'python'),'--weights-dir',str(self.root)]),0)
        output=read_worker_output(exchange,request)
        self.assertEqual(output['result']['status'],'failed');self.assertEqual(output['result']['observations'],[])
        self.assertNotIn('private-source-data',json.dumps(output))
        self.assertTrue(output['derived'])

    def test_supervised_driver_accepts_only_normal_main_zero_and_stops_children(self):
        marker=self.root/'child.pid'
        success=self.root/'success.py'
        success.write_text("def main(argv):\n import subprocess, sys\n from pathlib import Path\n child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n Path(argv[0]).write_text(str(child.pid))\n print('main-returned-zero',flush=True)\n return 0\n")
        command=[sys.executable,'-m','dorsopathy_workers.mri.tss_driver','--parent-script',str(success),'--',str(marker)]
        _infer(command,self.root/'success.log',5)
        self.assertIn('main-returned-zero',(self.root/'success.log').read_text())
        child_pid=int(marker.read_text())
        try:
            import os,time
            for _ in range(20):
                try: os.kill(child_pid,0)
                except ProcessLookupError: break
                time.sleep(.02)
            else:
                # A killed adopted child can remain a zombie until init reaps it.
                status=subprocess.run(['ps','-o','stat=','-p',str(child_pid)],capture_output=True,text=True).stdout.strip()
                self.assertTrue(not status or status.startswith('Z'),status)
        finally:
            try: os.kill(child_pid,9)
            except ProcessLookupError: pass
        for outcome in ("raise RuntimeError('main failed')","raise SystemExit(0)","return 7"):
            failure=self.root/'failure.py';failure.write_text('def main(argv):\n '+outcome+'\n')
            process=subprocess.run([sys.executable,'-m','dorsopathy_workers.mri.tss_driver','--parent-script',str(failure)],capture_output=True)
            self.assertNotEqual(process.returncode,0)

    @unittest.skipUnless(HAS_IMAGING and (PARENT/'backend/src/spine_backend/levels.py').is_file(),'Optional existing imaging runtime required')
    def test_parent_replay_recomputes_mask_volume_area_ap_and_rejects_geometry_or_cached_value_mismatch(self):
        import numpy as np
        import SimpleITK as sitk
        sys.path.insert(0,str(PARENT/'backend/src'))
        from spine_backend.levels import analyze_levels
        pixels=np.arange(12**3,dtype=np.float32).reshape(12,12,12)
        labels=np.zeros_like(pixels,dtype=np.uint16)
        labels[5:7,2:5,4:8]=95;labels[3:10,7:10,5:8]=2
        image=sitk.GetImageFromArray(pixels);mask=sitk.GetImageFromArray(labels)
        volume=self.root/'source_t2.mha';mask_path=self.root/'mask.mha'
        sitk.WriteImage(image,str(volume));sitk.WriteImage(mask,str(mask_path))
        series={'id':'synthetic-source-series','source_format':'mha','contrast':'T2','plane':'sagittal','acquisition_axis':0,'volume_path':str(volume),'source_frames':[]}
        anatomy=analyze_levels(volume,mask_path,self.root/'roi',series)
        payload={'series':series,'analysis':{'levels':anatomy['levels'],'segmentation':{'segmentation_path':str(mask_path),'provenance':None}}}
        data,refs=normalize_parent_export(payload,self.context,PARENT)
        source=self.store.add_bytes(json.dumps(data).encode(),'application/json','derived')
        result=analyze_source(data,self.request,self.context,source,refs)
        measured={obs['concept']:obs for obs in result['observations'] if obs['anatomy']['level']=='L4-L5'}
        self.assertEqual(measured['spine.disc.mask_volume']['value'],24)
        self.assertEqual(measured['spine.canal.cross_sectional_area']['value'],9)
        self.assertEqual(measured['spine.canal.ap_extent']['value'],3)
        bad=copy.deepcopy(payload)
        next(level for level in bad['analysis']['levels'] if level['level']=='L4-L5')['measurements'][0]['value']=999
        with self.assertRaises(ContractError): normalize_parent_export(bad,self.context,PARENT)
        mask.SetSpacing((2,1,1));sitk.WriteImage(mask,str(mask_path))
        with self.assertRaises(ValueError): normalize_parent_export(payload,self.context,PARENT)


if __name__=='__main__':unittest.main()
