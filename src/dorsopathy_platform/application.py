"""Episode facade: uploads, persisted runs and immutable reports."""
from copy import deepcopy
import uuid

from .api_contracts import canonical_bytes, opaque_id, validate_manifest
from .contracts import loads_json
from .storage import StorageError
from .uploads import UploadService

class EpisodeArtifactReader:
    """Read-only evidence access scoped to the report's selected episode."""
    def __init__(self,repository,artifacts,episode_id):
        self.repository,self.artifacts,self.episode_id=repository,artifacts,episode_id
    def get_artifact(self,episode_id,artifact_id):
        if episode_id!=self.episode_id:
            raise StorageError('Evidence belongs to a different episode')
        return self.repository.get_artifact(episode_id,artifact_id)
    def open(self,reference):
        saved=self.get_artifact(self.episode_id,reference['artifact_id'])
        if saved!=reference:
            raise StorageError('Evidence metadata does not match the episode catalog')
        return self.artifacts.open(saved)
    def read(self,reference):
        with self.open(reference) as stream:return stream.read()

class Application:
    def __init__(self, repository, artifacts, registry, dispatcher, report_builder,
                 max_upload_bytes=512 * 1024 * 1024):
        self.repository, self.artifacts, self.registry = repository, artifacts, registry
        self.dispatcher, self.report_builder = dispatcher, report_builder
        self.uploads = UploadService(repository, artifacts, max_bytes=max_upload_bytes)
        for run in repository.list_pending_runs():
            self.get_run(run['run_id'])

    def create_episode(self, episode_id=None):
        return self.repository.create_episode(opaque_id(episode_id or 'episode-' + uuid.uuid4().hex))

    def upload(self, episode_id, stream, media_type, declared_size=None):
        return self.uploads.upload(episode_id, stream, media_type, declared_size)

    def modules(self):
        return self.registry.describe()

    def start_run(self, manifest, idempotency_key):
        manifest = validate_manifest(manifest)
        opaque_id(idempotency_key)
        episode_id = manifest['episode_id']
        self.repository.get_episode(episode_id)
        if manifest['linkage_evidence_artifact_id']:
            self.repository.get_artifact(episode_id, manifest['linkage_evidence_artifact_id'])
        for result_id in manifest['reuse_result_ids']:
            reused = self.repository.get_result(result_id)
            if reused['episode_id'] != episode_id:
                raise StorageError('Reused result belongs to a different episode')
        run_id = 'run-' + uuid.uuid4().hex
        requests = []
        for index, module in enumerate(manifest['modules']):
            self.registry.get(module['module_id'], module['module_version'])
            inputs = [self.repository.get_artifact(episode_id, artifact_id)
                      for artifact_id in module['input_artifact_ids']]
            requests.append({
                'schema_version':'1.0', 'job_id':run_id + '.job-' + str(index + 1),
                'episode_id':episode_id, 'module_id':module['module_id'],
                'module_version':module['module_version'], 'input_artifacts':inputs,
                'parameters':deepcopy(module['parameters']), 'protocol_id':manifest['protocol_id'],
            })
        run = self.repository.create_run(episode_id, idempotency_key, manifest,
                                         job_requests=requests, run_id=run_id)
        for job_id in run['job_ids']:
            self.dispatcher.enqueue(job_id)
        return run

    def get_run(self, run_id):
        run = self.repository.get_run(run_id)
        if run['status'] != 'running':
            return run
        jobs = [self.repository.get_job(job_id) for job_id in run['job_ids']]
        if any(job['status'] in {'queued','running'} for job in jobs):
            return run
        result_ids = list(run['manifest']['reuse_result_ids'])
        for job in jobs:
            if job['result_id']:
                result_ids.append(job['result_id'])
        for result_id in result_ids:
            result = self.repository.get_result(result_id)
            for ref in result['artifacts']:
                with self.artifacts.open(ref):
                    pass
                self.repository.register_artifact(run['episode_id'], ref)
        snapshot = self.repository.create_snapshot(run['episode_id'], result_ids)
        reader=EpisodeArtifactReader(self.repository,self.artifacts,run['episode_id'])
        report = self.report_builder(snapshot, run['manifest'], reader)
        report_ref = self.artifacts.add_bytes(canonical_bytes(report), 'application/json', role='derived')
        self.repository.register_artifact(run['episode_id'], report_ref)
        statuses = [job['status'] for job in jobs]
        status = 'complete'
        if any(state in {'failed','partial'} for state in statuses):
            status = 'failed' if statuses and all(state=='failed' for state in statuses) else 'partial'
        return self.repository.finalize_run(run_id, snapshot['snapshot_id'], report_ref, status)

    def get_job(self, job_id):
        return self.repository.get_job(job_id)

    def get_result(self, result_id):
        return self.repository.get_result(result_id)

    def get_snapshot(self, snapshot_id):
        return self.repository.get_snapshot(snapshot_id)

    def get_report(self, run_id):
        run = self.get_run(run_id)
        if run['report_artifact'] is None:
            raise StorageError('Report is not available yet')
        ref = self.repository.get_artifact(run['episode_id'], run['report_artifact']['artifact_id'])
        if ref['size_bytes'] > 16 * 1024 * 1024:
            raise StorageError('Report exceeds the supported size')
        return loads_json(self.artifacts.read(ref))

    def open_artifact(self, episode_id, artifact_id):
        return self.uploads.open(episode_id, artifact_id)

    def close(self):
        self.dispatcher.close(wait=True)
