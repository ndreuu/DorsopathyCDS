import unittest
from dorsopathy_platform.api_contracts import default_manifest, validate_manifest, manifest_sha256
from dorsopathy_platform.contracts import ContractError

class ManifestTests(unittest.TestCase):
    def manifest(self):
        return default_manifest('episode-a', [{'module_id':'complaints','module_version':'0.1.0','input_artifact_ids':['artifact-a'],'parameters':{}}])
    def test_canonical_hash_and_isolated_copy(self):
        m=self.manifest(); validated=validate_manifest(m)
        self.assertEqual(manifest_sha256(m),manifest_sha256(dict(reversed(list(m.items())))))
        validated['modules'][0]['parameters']['x']=1
        self.assertEqual(m['modules'][0]['parameters'],{})
    def test_linkage_and_duplicate_input_gates(self):
        m=self.manifest(); m['mode']='replay'
        with self.assertRaises(ContractError): validate_manifest(m)
        m['linkage']='verified_linked'
        with self.assertRaises(ContractError): validate_manifest(m)
        m['linkage']='unlinked'; m['modules'][0]['input_artifact_ids']=['a','a']
        with self.assertRaises(ContractError): validate_manifest(m)
    def test_extra_paths_and_unsupported_selection_rejected(self):
        m=self.manifest(); m['path']='/tmp/input'
        with self.assertRaises(ContractError): validate_manifest(m)
        m=self.manifest(); m['clinical_selection']={'spine.disc.bulging':'o1'}
        with self.assertRaises(ContractError): validate_manifest(m)
    def test_non_finite_parameters_rejected(self):
        m=self.manifest(); m['modules'][0]['parameters']={'x':float('nan')}
        with self.assertRaises(ContractError): validate_manifest(m)
