"""Bounded local import of one explicitly synthetic multimodal patient ZIP.

This format supplies sources, never commands/models or precomputed results.
The application runs its registered providers and creates its ordinary snapshot.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
from io import BytesIO
from pathlib import Path
import re
import stat
import uuid
import zipfile

from .api_contracts import canonical_bytes, default_manifest, opaque_id
from .contracts import loads_json

MAX_TOTAL = 64 * 1024 * 1024
MAX_SOURCE = 8 * 1024 * 1024
PROFILES = {
    'complaints': ('0.2.0', 'complaints.screening.v2'),
    'clinical.exam': ('0.1.0', 'clinical-exam.v1'),
    'mri.anatomy': ('0.1.0', 'mri.source-grid.v1'),
    'emg.surface': ('0.2.0', 'emg-raw-csv.v2'),
    'thermography': ('0.2.0', 'thermography-raw-flir.v1'),
    'ultrasound': ('0.2.0', 'ultrasound-raw.v1'),
    'lidar.posture': ('0.2.0', 'lidar-depth.v2'),
    'nociception': ('0.2.0', 'nociception-vendor-csv.v2'),
}
BINDINGS = {
    'complaints': set(), 'clinical.exam': set(), 'mri.anatomy': set(),
    'emg.surface': {('csv_artifact_id','text/csv')},
    'nociception': {('csv_artifact_id','text/csv')},
    'thermography': {('jpeg_artifact_id','image/jpeg')},
    'ultrasound': {('frame_artifact_id','image/png')},
    'lidar.posture': {('depth_artifact_id','image/png'),('camera_artifact_id','application/json')},
}


def _path(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', value):
        raise ValueError('Unsupported bundle path')
    if value.startswith('/') or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ValueError('Bundle paths must be normalized relative file paths')
    return value


def _object(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ValueError('Unsupported synthetic bundle fields')


def _source(archive, entry, files, case):
    item = files[entry['source_path']]
    if item['media_type'] != 'application/json' or item['size_bytes'] > MAX_SOURCE:
        raise ValueError('Source JSON exceeds the supported profile')
    source = loads_json(archive.read(entry['source_path']))
    module = entry['module_id']
    if type(source) is not dict or source.get('source_kind') != PROFILES[module][1]:
        raise ValueError('Bundle source profile does not match module')
    if module == 'mri.anatomy':
        if source.get('mode') != 'synthetic_demo' or source.get('model') is not None:
            raise ValueError('MRI must remain an explicit synthetic phantom')
        metadata = source.get('comparison_metadata')
        if type(metadata) is not dict or any(metadata.get(key) != case[key] for key in ('subject_ref','session_ref','protocol_id')):
            raise ValueError('Synthetic MRI identity/protocol mismatch')
    elif source.get('origin') != 'synthetic_demo':
        raise ValueError('Bundle source must remain explicitly synthetic')
    if source.get('acquired_at') != case['acquired_at']:
        raise ValueError('Source acquisition time does not match synthetic case')
    if module not in {'complaints', 'clinical.exam', 'mri.anatomy'}:
        for key in ('subject_ref', 'session_ref', 'protocol_id'):
            if source.get(key) != case[key]:
                raise ValueError('Synthetic source identity/protocol mismatch')
    for binding in entry['artifacts']:
        if binding['field'] not in source or type(source[binding['field']]) is not str:
            raise ValueError('Binding must match a source artifact identifier field')
    return source


def inspect_bundle(archive):
    infos = archive.infolist()
    if not 2 <= len(infos) <= 65:
        raise ValueError('Synthetic archive exceeds file count profile')
    index = {}
    total = 0
    for info in infos:
        name = _path(info.filename)
        if name in index or info.is_dir() or info.flag_bits & 1:
            raise ValueError('Duplicate, directory or encrypted archive member')
        if stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}:
            raise ValueError('Only regular archive files are supported')
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} or not 0 < info.file_size <= MAX_TOTAL:
            raise ValueError('Unsupported archive compression or file size')
        total += info.file_size
        if total > MAX_TOTAL:
            raise ValueError('Synthetic archive exceeds total size profile')
        index[name] = info
    if 'case.json' not in index or index['case.json'].file_size > 65536:
        raise ValueError('A bounded case.json is required')
    case = loads_json(archive.read('case.json'))
    fields = {'schema_version', 'source_kind', 'case_id', 'origin', 'subject_ref',
              'session_ref', 'protocol_id', 'acquired_at', 'title', 'modules', 'files'}
    _object(case, fields)
    if (case['schema_version'], case['source_kind'], case['origin']) != ('1.0', 'synthetic.patient-bundle.v1', 'synthetic_demo'):
        raise ValueError('Only an explicitly synthetic patient bundle is supported')
    for key in ('case_id', 'subject_ref', 'session_ref', 'protocol_id'):
        opaque_id(case[key])
    if type(case['acquired_at']) is not str or type(case['title']) is not str or len(case['title']) > 200:
        raise ValueError('Invalid synthetic case description')
    try:
        parsed = datetime.fromisoformat(case['acquired_at'].replace('Z','+00:00'))
        if parsed.utcoffset() is None:raise ValueError('Unknown timezone')
    except ValueError:
        raise ValueError('Synthetic case needs an explicit acquisition timezone') from None
    if type(case['files']) is not list or type(case['modules']) is not list or len(case['modules']) != len(PROFILES):
        raise ValueError('Synthetic patient requires the eight declared modules')
    files = {}
    for item in case['files']:
        _object(item, {'path', 'media_type', 'sha256', 'size_bytes'})
        name = _path(item['path'])
        if name in files or name == 'case.json' or name not in index:
            raise ValueError('Bundle file inventory is ambiguous')
        if type(item['size_bytes']) is not int or item['size_bytes'] != index[name].file_size:
            raise ValueError('Bundle declared size mismatch')
        if type(item['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid bundle checksum')
        if type(item['media_type']) is not str or item['media_type'] not in {'application/json','image/png','image/jpeg','text/csv','text/markdown','application/xml'}:
            raise ValueError('Unsupported bundle media type')
        digest = hashlib.sha256(); count = 0
        with archive.open(name) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                count += len(chunk)
                if count > item['size_bytes']:
                    raise ValueError('Bundle expanded size mismatch')
                digest.update(chunk)
        if count != item['size_bytes'] or digest.hexdigest() != item['sha256']:
            raise ValueError('Bundle content checksum mismatch')
        files[name] = item
    if set(index) != set(files) | {'case.json'}:
        raise ValueError('Archive has undeclared files')
    seen = set(); sources = {}
    for entry in case['modules']:
        _object(entry, {'module_id','module_version','source_path','artifacts','parameters'})
        module = entry['module_id']
        if type(module) is not str or module not in PROFILES or module in seen or entry['module_version'] != PROFILES[module][0]:
            raise ValueError('Unsupported or duplicate bundle module')
        seen.add(module)
        if _path(entry['source_path']) not in files or type(entry['artifacts']) is not list or len(entry['artifacts']) > 2:
            raise ValueError('Invalid module source or artifact list')
        expected = {'processing_mode':'replay'} if module == 'mri.anatomy' else {}
        if entry['parameters'] != expected:
            raise ValueError('Archive must not supply arbitrary provider parameters')
        bindings = set()
        for binding in entry['artifacts']:
            _object(binding, {'field','media_type','path'})
            name = _path(binding['path'])
            if type(binding['field']) is not str or binding['field'] in bindings or name not in files or binding['media_type'] != files[name]['media_type']:
                raise ValueError('Invalid module artifact binding')
            bindings.add(binding['field'])
        if {(b['field'],b['media_type']) for b in entry['artifacts']} != BINDINGS[module]:
            raise ValueError('Module bindings must match documented raw artifact fields')
        sources[module] = _source(archive, entry, files, case)
    return deepcopy(case), sources


def start_synthetic_bundle(application, path):
    """Validate every byte before creating an episode; stream without extracting."""
    path = Path(path)
    if path.stat().st_size > MAX_TOTAL:
        raise ValueError('Synthetic ZIP exceeds supported compressed size')
    with path.open('rb') as raw, zipfile.ZipFile(raw) as archive:
        case, sources = inspect_bundle(archive)
        supported = {(p['module_id'], p['module_version']) for p in application.modules()}
        if any((e['module_id'], e['module_version']) not in supported for e in case['modules']):
            raise ValueError('Configure all raw workers before importing this synthetic patient')
        episode = application.create_episode()['episode_id']
        raw.seek(0)
        archive_ref = application.upload(episode, raw, 'application/zip', path.stat().st_size)
        case_bytes = canonical_bytes(case)
        case_ref = application.upload(episode, BytesIO(case_bytes), 'application/json', len(case_bytes))
        modules = []
        for entry in case['modules']:
            source = deepcopy(sources[entry['module_id']]); refs = []
            for binding in entry['artifacts']:
                info = archive.getinfo(binding['path'])
                with archive.open(info) as stream:
                    ref = application.upload(episode, stream, binding['media_type'], info.file_size)
                source[binding['field']] = ref['artifact_id']; refs.append(ref['artifact_id'])
            content = canonical_bytes(source)
            source_ref = application.upload(episode, BytesIO(content), 'application/json', len(content))
            modules.append({'module_id':entry['module_id'], 'module_version':entry['module_version'],
                            'input_artifact_ids':[source_ref['artifact_id'], *refs], 'parameters':deepcopy(entry['parameters'])})
        manifest = default_manifest(episode, modules, mode='synthetic', linkage='synthetic_linked')
        manifest['protocol_id'] = case['protocol_id']
        run = application.start_run(manifest, 'synthetic-bundle-'+uuid.uuid4().hex)
        return run, {'case':case, 'archive':archive_ref, 'case_artifact':case_ref}
