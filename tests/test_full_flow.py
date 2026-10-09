"""Integration acceptance uses the production registry on synthetic raw inputs."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dorsopathy_platform.api_contracts import canonical_bytes, default_manifest
from dorsopathy_platform.bootstrap import create_application
from dorsopathy_platform.demo_flow import start_demo, start_review_demo
from dorsopathy_platform.workflow import wait_for_run

class FullFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.app=create_application(self.directory.name)
    def tearDown(self):
        self.app.close();self.directory.cleanup()
    def execute(self,run):
        finished=wait_for_run(self.app,run['run_id'],timeout_seconds=20)
        return finished,self.app.get_snapshot(finished['snapshot_id']),self.app.get_report(run['run_id'])
    def test_actual_eight_providers_measure_then_review_same_mri(self):
        first,snapshot,report=self.execute(start_demo(self.app))
        self.assertEqual(len(snapshot['results']),8)
        self.assertEqual({r['module_id'] for r in snapshot['results']},
            {'complaints','clinical.exam','mri.anatomy','lidar.posture','thermography','emg.surface','ultrasound','nociception'})
        for result in snapshot['results']:
            self.assertNotEqual(result['status'],'failed',result)
        values={}
        for result in snapshot['results']:
            for obs in result['observations']:
                if obs['assessment_status']=='assessed':
                    values.setdefault(obs['concept'],[]).append(obs['value'])
                    self.assertTrue(obs['evidence_refs']);self.assertEqual(obs['origin'],'synthetic_demo')
        self.assertEqual(values['symptoms.pain_intensity'],[6])
        self.assertAlmostEqual(values['spine.canal.cross_sectional_area'][0],4.8)
        self.assertEqual(values['posture.landmark_distance'],[500.0])
        self.assertEqual(values['posture.landmark_angle'],[90.0])
        self.assertEqual(sorted(values['thermal.roi_mean_temperature']),[32.0,34.0])
        self.assertEqual(values['thermal.left_right_delta'],[-2.0])
        self.assertEqual(values['ultrasound.distance'],[5.0])
        self.assertEqual(values['ultrasound.area'],[12.0])
        self.assertTrue(all(abs(v-7.0710678118654755)<1e-7 for v in values['emg.rms']))
        self.assertTrue(all(abs(v-125)<1e-7 for v in values['emg.median_frequency']))
        self.assertEqual(values['nociception.device_index'],[40.0])
        self.assertEqual(report['clinical_report']['results'][0]['status'],'indeterminate')
        frozen=canonical_bytes(report)
        second,after,reviewed=self.execute(start_review_demo(self.app,first['run_id']))
        self.assertEqual(len(second['job_ids']),1)
        mri=lambda s:next(r['result_id'] for r in s['results'] if r['module_id']=='mri.anatomy')
        self.assertEqual(mri(snapshot),mri(after))
        self.assertNotEqual(snapshot['snapshot_id'],after['snapshot_id'])
        self.assertEqual(reviewed['clinical_report']['results'][0]['status'],'met')
        self.assertEqual(canonical_bytes(self.app.get_report(first['run_id'])),frozen)
        self.assertTrue(all(c['exact_quote'] for c in reviewed['clinical_report']['results'][0]['citations']))
        for result in after['results']:
            for ref in result['artifacts']:
                saved,stream=self.app.open_artifact(first['episode_id'],ref['artifact_id'])
                with stream:self.assertEqual(len(stream.read()),saved['size_bytes'])
        replay=deepcopy(second['manifest']);replay.update(modules=[],reuse_result_ids=[r['result_id'] for r in after['results']],mode='replay',linkage='unlinked')
        _,_,unlinked=self.execute(self.app.start_run(replay,'unlinked'))
        self.assertEqual(unlinked['clinical_report']['results'][0]['status'],'indeterminate')
    def test_bad_module_does_not_drop_other_results(self):
        episode=self.app.create_episode()['episode_id']
        from dorsopathy_platform.demo_flow import demo_inputs
        source=next(e for e in demo_inputs() if e['module_id']=='complaints')['sources'][0]
        ids=[]
        for value in [source,{'bad':'MRI profile'}]:
            data=canonical_bytes(value);ids.append(self.app.upload(episode,BytesIO(data),'application/json',len(data))['artifact_id'])
        manifest=default_manifest(episode,[{'module_id':module,'module_version':'0.1.0','input_artifact_ids':[aid],'parameters':{}}
                                         for module,aid in zip(['complaints','mri.anatomy'],ids)])
        run,snapshot,report=self.execute(self.app.start_run(manifest,'partial'))
        self.assertEqual(run['status'],'partial')
        self.assertEqual(sorted(r['status'] for r in snapshot['results']),['complete','failed'])
        self.assertEqual(report['clinical_report']['results'][0]['status'],'indeterminate')
        failed=next(result for result in snapshot['results'] if result['status']=='failed')
        reused=deepcopy(manifest);reused.update(modules=[],reuse_result_ids=[failed['result_id']])
        reuse_run,_,_=self.execute(self.app.start_run(reused,'reuse-failed'))
        self.assertEqual(reuse_run['status'],'failed')
        reused['reuse_result_ids']=[result['result_id'] for result in snapshot['results']]
        reuse_run,_,_=self.execute(self.app.start_run(reused,'reuse-mixed'))
        self.assertEqual(reuse_run['status'],'partial')
    def test_bootstrap_does_not_import_heavy_mri_libraries(self):
        code="from dorsopathy_platform.bootstrap import create_application;import sys,tempfile;d=tempfile.TemporaryDirectory();a=create_application(d.name);assert not {'torch','SimpleITK','nnunetv2','PySide6'}&set(sys.modules);a.close();d.cleanup()"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
