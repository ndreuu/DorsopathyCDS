from pathlib import Path
import tempfile
import unittest

from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_platform.bootstrap import create_application
from dorsopathy_platform.bundle_import import start_bundle
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_platform.workflow import wait_for_run

class BundleImportTests(unittest.TestCase):
    def test_replay_and_modified_evidence(self):
        from dorsopathy_modules.mri_anatomy.synthetic import synthetic_source
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle';store=ArtifactStore(bundle)
            ref=store.add_bytes(canonical_bytes(synthetic_source()),'application/json')
            upload={'schema_version':'1.0','processing':'replay','first_artifact_id':ref['artifact_id'],
                    'files':[{'artifact_id':ref['artifact_id'],'relative_path':ref['storage_key'],
                              'media_type':ref['media_type'],'sha256':ref['sha256'],'size_bytes':ref['size_bytes']}],
                    'linkage':'unlinked','clinical_use':'research_only'}
            (bundle/'upload-manifest.json').write_bytes(canonical_bytes(upload))
            app=create_application(root/'runtime')
            try:
                run=wait_for_run(app,start_bundle(app,bundle)['run_id'],timeout_seconds=10)
                self.assertNotEqual(run['status'],'failed')
                self.assertEqual(run['manifest']['linkage'],'unlinked')
                self.assertEqual(app.get_report(run['run_id'])['clinical_report']['results'][0]['status'],'indeterminate')
                (bundle/ref['storage_key']).write_bytes(b'tampered')
                with self.assertRaises(ValueError):start_bundle(app,bundle)
                upload['files'][0]['relative_path']='../outside'
                (bundle/'upload-manifest.json').write_bytes(canonical_bytes(upload))
                with self.assertRaises(ValueError):start_bundle(app,bundle)
            finally:app.close()
