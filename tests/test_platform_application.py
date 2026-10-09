import io
from pathlib import Path
import tempfile
import threading
import unittest

from dorsopathy_platform.api_contracts import default_manifest
from dorsopathy_platform.application import Application
from dorsopathy_platform.contracts import validate_result
from dorsopathy_platform.dispatcher import Dispatcher
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.registry import ModuleRegistry
from dorsopathy_platform.results import result_envelope,observation_id_for
from dorsopathy_platform.storage import ArtifactStore,SqliteRepository,ConflictError,StorageError
from dorsopathy_platform.workflow import wait_for_run

class ArithmeticFixture:
    spec=ProviderSpec('test.arithmetic','0.1.0','Test arithmetic',synthetic_only=True)
    def analyze(self,request,context):
        n=int(context.artifacts.read(request['input_artifacts'][0]))
        obs={'observation_id':observation_id_for(request['job_id'],'test.square'),
             'concept':'test.square','assessment_status':'assessed','value':n*n,'unit':'1',
             'anatomy':{'region':'lower_back','side':'unspecified','level':None,'numbering_status':'not_applicable'},
             'quality':'usable','origin':'synthetic_demo','method':'square-test-value',
             'evidence_refs':[{'artifact_id':request['input_artifacts'][0]['artifact_id'],'selector':{'kind':'text'}}],
             'model':None,'acquired_at':None,'coordinate_frame':None}
        return validate_result(result_envelope(request,[obs],[]),request)

def fixture_report(snapshot,manifest,artifacts):
    return {'snapshot_id':snapshot['snapshot_id'],'manifest':manifest,'values':[
        o['value'] for r in snapshot['results'] for o in r['observations']]}

class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.repo=SqliteRepository(self.root);self.blobs=ArtifactStore(self.root)
        self.registry=ModuleRegistry();self.registry.register(ArithmeticFixture())
        self.dispatcher=Dispatcher(self.repo,self.blobs,self.registry,recover=True)
        self.app=Application(self.repo,self.blobs,self.registry,self.dispatcher,fixture_report)
        self.app.create_episode('episode-a')
    def tearDown(self): self.app.close(); self.tmp.cleanup()
    def manifest(self,n=3):
        ref=self.app.upload('episode-a',io.BytesIO(str(n).encode()),'text/plain')
        return default_manifest('episode-a',[{'module_id':'test.arithmetic','module_version':'0.1.0','input_artifact_ids':[ref['artifact_id']],'parameters':{}}])
    def test_run_is_idempotent_and_old_snapshot_does_not_use_later_results(self):
        m=self.manifest(); first=self.app.start_run(m,'first')
        self.assertEqual(first['run_id'],self.app.start_run(m,'first')['run_id'])
        first=wait_for_run(self.app,first['run_id'],timeout_seconds=3)
        report=self.app.get_report(first['run_id']);self.assertEqual(report['values'],[9])
        second=wait_for_run(self.app,self.app.start_run(self.manifest(5),'second')['run_id'],timeout_seconds=3)
        self.assertEqual(self.app.get_report(second['run_id'])['values'],[25])
        self.assertEqual(self.app.get_report(first['run_id']),report)
        with self.assertRaises(ConflictError): self.app.start_run(self.manifest(4),'first')
    def test_parallel_polling_publishes_identical_refs(self):
        run=self.app.start_run(self.manifest(),'poll')
        for job_id in run['job_ids']:
            future=self.dispatcher.enqueue(job_id)
            if future is not None:future.result(timeout=3)
        self.dispatcher.close(wait=True)
        results=[];errors=[]
        def poll():
            try: results.append(self.app.get_run(run['run_id']))
            except Exception as e: errors.append(type(e).__name__)
        threads=[threading.Thread(target=poll) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[])
        self.assertEqual(len({r['snapshot_id'] for r in results}),1)
        self.assertEqual(len({r['report_artifact']['artifact_id'] for r in results}),1)
    def test_restart_finishes_completed_jobs_and_reuse_is_episode_scoped(self):
        run=self.app.start_run(self.manifest(),'restart')
        for job_id in run['job_ids']:
            future=self.dispatcher.enqueue(job_id)
            if future is not None:future.result(timeout=3)
        self.app.close()
        dispatcher=Dispatcher(self.repo,self.blobs,self.registry,recover=True)
        app=Application(self.repo,self.blobs,self.registry,dispatcher,fixture_report)
        self.app=app; finished=app.get_run(run['run_id'])
        self.assertEqual(finished['status'],'complete')
        result=self.repo.get_snapshot(finished['snapshot_id'])['results'][0]
        m=dict(finished['manifest']);m['modules']=[];m['reuse_result_ids']=[result['result_id']]
        # A reuse-only manifest is constructed before validation, preserving no extra fields.
        reuse=app.start_run(m,'reuse');self.assertEqual(app.get_run(reuse['run_id'])['snapshot_id'],finished['snapshot_id'])
        app.create_episode('episode-b');m['episode_id']='episode-b'
        with self.assertRaises(StorageError):app.start_run(m,'wrong-episode')
