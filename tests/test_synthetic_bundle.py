"""Archive transport checks; no model prediction or diagnostic validation here."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from dorsopathy_platform.api_contracts import canonical_bytes, validate_manifest
from dorsopathy_platform.synthetic_bundle import BINDINGS, PROFILES, inspect_bundle, start_synthetic_bundle


TIME='2026-01-01T12:00:00+00:00'


def inventory(case, parts):
    media=case.pop('_media',None) or {f['path']:f['media_type'] for f in case['files']}
    case['files']=[{'path':name,'media_type':media[name],'size_bytes':len(content),
                    'sha256':hashlib.sha256(content).hexdigest()} for name,content in sorted(parts.items())]


def fixture():
    parts={'README.md':b'Explicitly synthetic transport fixture.'};media={'README.md':'text/markdown'};entries=[]
    for module,(version,kind) in sorted(PROFILES.items()):
        base='inputs/'+module
        source={'source_kind':kind,'origin':'synthetic_demo','acquired_at':TIME,
                'subject_ref':'SYNTHETIC-001','session_ref':'SESSION-001','protocol_id':'synthetic-patient-v1'}
        if module=='mri.anatomy':
            source.update(mode='synthetic_demo',model=None,comparison_metadata={key:source[key] for key in ('subject_ref','session_ref','protocol_id')})
            del source['origin']
        bindings=[]
        for field,mime in sorted(BINDINGS[module]):
            path=base+'/'+field+'.dat';parts[path]=('transport-only fixture '+module+'/'+field).encode();media[path]=mime
            source[field]='REPLACE_AFTER_UPLOAD'
            bindings.append({'field':field,'media_type':mime,'path':path})
        path=base+'/source.json';parts[path]=canonical_bytes(source);media[path]='application/json'
        entries.append({'module_id':module,'module_version':version,'source_path':path,'artifacts':bindings,
                        'parameters':{'processing_mode':'replay'} if module=='mri.anatomy' else {}})
    case={'schema_version':'1.0','source_kind':'synthetic.patient-bundle.v1','case_id':'synthetic-001',
          'origin':'synthetic_demo','subject_ref':'SYNTHETIC-001','session_ref':'SESSION-001',
          'protocol_id':'synthetic-patient-v1','acquired_at':TIME,'title':'Synthetic transport checks',
          'modules':entries,'files':[],'_media':media}
    inventory(case,parts)
    return case,parts


def zipped(case,parts,extra=None,symlink=None,duplicate=None):
    buffer=BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(buffer,'w') as archive:
            for name,content in dict(parts,**{'case.json':canonical_bytes(case)}).items():
                info=zipfile.ZipInfo(name)
                info.external_attr=((stat.S_IFLNK if name==symlink else stat.S_IFREG)|0o644)<<16
                archive.writestr(info,content)
            if extra:archive.writestr(extra,b'undeclared')
            if duplicate:archive.writestr(duplicate,parts[duplicate])
    return buffer.getvalue()


def inspect(content):
    with zipfile.ZipFile(BytesIO(content)) as archive:return inspect_bundle(archive)


class RecordingApplication:
    """Transport recorder, never an inference provider."""
    def __init__(self,missing=None):self.created=0;self.uploads={};self.missing=missing;self.manifest=None
    def modules(self):return [{'module_id':m,'module_version':v} for m,(v,k) in PROFILES.items() if m!=self.missing]
    def create_episode(self):self.created+=1;return {'episode_id':'episode-transport-test'}
    def upload(self,episode,stream,media_type,declared_size):
        content=stream.read();sha=hashlib.sha256(content).hexdigest();aid='artifact-'+sha
        assert len(content)==declared_size
        self.uploads[aid]=(media_type,content)
        return {'artifact_id':aid,'sha256':sha,'size_bytes':len(content)}
    def start_run(self,manifest,idempotency_key):
        self.manifest=validate_manifest(manifest);return {'run_id':'run-transport-test','episode_id':manifest['episode_id']}


class SyntheticBundleTests(unittest.TestCase):
    def test_inventory_and_sources_are_preserved_without_extraction(self):
        case,parts=fixture();saved,sources=inspect(zipped(case,parts))
        self.assertEqual(saved,case);self.assertEqual(set(sources),set(PROFILES))
        self.assertEqual(sources['emg.surface']['csv_artifact_id'],'REPLACE_AFTER_UPLOAD')

    def test_streamed_upload_rebinds_only_declared_raw_fields_and_starts_one_case(self):
        case,parts=fixture();app=RecordingApplication()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'patient.zip';path.write_bytes(zipped(case,parts))
            run,bundle=start_synthetic_bundle(app,path)
        self.assertEqual(app.created,1);self.assertEqual(len(app.manifest['modules']),8)
        self.assertEqual(app.manifest['mode'],'synthetic');self.assertEqual(app.manifest['linkage'],'synthetic_linked')
        self.assertEqual(app.manifest['protocol_id'],case['protocol_id'])
        for entry in app.manifest['modules']:
            first,*raw_ids=entry['input_artifact_ids'];source=json.loads(app.uploads[first][1])
            original=next(e for e in case['modules'] if e['module_id']==entry['module_id'])
            self.assertEqual(source.get('origin','synthetic_demo'),'synthetic_demo')
            for binding,aid in zip(original['artifacts'],raw_ids):
                self.assertEqual(source[binding['field']],aid)
                self.assertEqual(app.uploads[aid],(binding['media_type'],parts[binding['path']]))
        self.assertEqual(bundle['archive']['sha256'],hashlib.sha256(zipped(case,parts)).hexdigest())

    def test_hash_mismatch_is_rejected_before_creating_episode(self):
        case,parts=fixture();parts['README.md']+=b'tamper';app=RecordingApplication()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'patient.zip';path.write_bytes(zipped(case,parts))
            with self.assertRaises(ValueError):start_synthetic_bundle(app,path)
        self.assertEqual(app.created,0);self.assertFalse(app.uploads)

    def test_missing_raw_registration_fails_before_creating_episode(self):
        case,parts=fixture();app=RecordingApplication(missing='ultrasound')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'patient.zip';path.write_bytes(zipped(case,parts))
            with self.assertRaises(ValueError):start_synthetic_bundle(app,path)
        self.assertEqual(app.created,0)

    def test_symlinks_duplicate_names_and_undeclared_members_are_rejected(self):
        case,parts=fixture()
        for options in ({'symlink':'README.md'},{'duplicate':'README.md'},{'extra':'extra.txt'}):
            with self.subTest(options=options),self.assertRaises(ValueError):inspect(zipped(case,parts,**options))

    def test_unsafe_relative_paths_are_rejected(self):
        case,parts=fixture()
        for name in ('../outside','/absolute','inputs//bad','inputs/./bad','C:drive','inputs\\bad'):
            with self.subTest(name=name),self.assertRaises(ValueError):inspect(zipped(case,parts,extra=name))

    def test_real_origin_cannot_be_relabelled_as_synthetic_case(self):
        case,parts=fixture();path='inputs/emg.surface/source.json';source=json.loads(parts[path]);source['origin']='automatically_measured'
        parts[path]=canonical_bytes(source);inventory(case,parts)
        with self.assertRaises(ValueError):inspect(zipped(case,parts))

    def test_mri_subject_mismatch_is_rejected_even_with_valid_content_hash(self):
        case,parts=fixture();path='inputs/mri.anatomy/source.json';source=json.loads(parts[path]);source['comparison_metadata']['subject_ref']='OTHER-SUBJECT'
        parts[path]=canonical_bytes(source);inventory(case,parts)
        with self.assertRaises(ValueError):inspect(zipped(case,parts))

    def test_raw_protocol_and_time_must_match_case(self):
        for key,value in (('protocol_id','wrong-protocol'),('acquired_at','2026-01-02T12:00:00+00:00')):
            case,parts=fixture();path='inputs/emg.surface/source.json';source=json.loads(parts[path]);source[key]=value
            parts[path]=canonical_bytes(source);inventory(case,parts)
            with self.subTest(key=key),self.assertRaises(ValueError):inspect(zipped(case,parts))

    def test_binding_cannot_overwrite_origin(self):
        case,parts=fixture();entry=next(e for e in case['modules'] if e['module_id']=='emg.surface');entry['artifacts'][0]['field']='origin'
        with self.assertRaises(ValueError):inspect(zipped(case,parts))

    def test_commands_parameters_and_missing_modules_are_rejected(self):
        case,parts=fixture();bad=deepcopy(case);bad['command']=['untrusted']
        with self.assertRaises(ValueError):inspect(zipped(bad,parts))
        bad=deepcopy(case);bad['modules'][0]['parameters']={'checkpoint':'untrusted'}
        with self.assertRaises(ValueError):inspect(zipped(bad,parts))
        bad=deepcopy(case);bad['modules'].pop()
        with self.assertRaises(ValueError):inspect(zipped(bad,parts))

    def test_unknown_timezone_is_rejected(self):
        case,parts=fixture();case['acquired_at']='2026-01-01T12:00:00'
        with self.assertRaises(ValueError):inspect(zipped(case,parts))


if __name__=='__main__':unittest.main()
