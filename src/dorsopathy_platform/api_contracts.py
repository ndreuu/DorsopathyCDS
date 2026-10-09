"""Workflow transport contracts, separate from unchanged ModuleResult 1.0."""
from copy import deepcopy
import hashlib
import json
import re

from .contracts import ContractError, loads_json

FACT_IDS = frozenset({
    'clinical.specific_cause_warning_signs_absent',
    'clinical.radiculopathy_signs_and_positive_tension_tests',
    'clinical.imaging_clinical_concordance', 'imaging.study_status',
})
_ID = re.compile(r'[A-Za-z0-9_.-]{1,100}\Z', re.ASCII)
_FIELDS = {'schema_version', 'episode_id', 'population', 'mode', 'linkage',
           'linkage_evidence_artifact_id', 'modules', 'reuse_result_ids',
           'clinical_selection', 'protocol_id'}

def opaque_id(value):
    if type(value) is not str or not _ID.fullmatch(value) or value in {'.', '..'}:
        raise ContractError('Invalid opaque identifier')
    return value

def canonical_bytes(value):
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        loads_json(data)
        return data
    except (ValueError, TypeError, RecursionError):
        raise ContractError('Payload must contain finite JSON') from None

def manifest_sha256(manifest):
    return hashlib.sha256(canonical_bytes(validate_manifest(manifest))).hexdigest()

def validate_manifest(value):
    if type(value) is not dict or set(value) != _FIELDS:
        raise ContractError('CaseManifest requires exactly documented fields')
    value = loads_json(canonical_bytes(value))
    if value['schema_version'] != '1.0' or value['population'] != 'adult':
        raise ContractError('Unsupported manifest version or population')
    opaque_id(value['episode_id'])
    if type(value['mode']) is not str or value['mode'] not in {'synthetic', 'replay', 'live'}:
        raise ContractError('Unsupported processing mode')
    if type(value['linkage']) is not str or value['linkage'] not in {'synthetic_linked', 'verified_linked', 'unlinked', 'unknown'}:
        raise ContractError('Unsupported linkage state')
    link_ref = value['linkage_evidence_artifact_id']
    if link_ref is not None:
        opaque_id(link_ref)
    if value['linkage'] == 'verified_linked' and link_ref is None:
        raise ContractError('Verified linkage needs a source attestation artifact')
    if value['linkage'] == 'synthetic_linked' and value['mode'] != 'synthetic':
        raise ContractError('Synthetic linkage requires synthetic mode')
    if value['protocol_id'] is not None:
        opaque_id(value['protocol_id'])
    if type(value['modules']) is not list or not value['modules'] and not value['reuse_result_ids']:
        raise ContractError('Manifest requires modules or reused results')
    seen = set()
    for module in value['modules']:
        if type(module) is not dict or set(module) != {'module_id', 'module_version', 'input_artifact_ids', 'parameters'}:
            raise ContractError('Invalid module selection')
        opaque_id(module['module_id']); opaque_id(module['module_version'])
        if module['module_id'] in seen:
            raise ContractError('Duplicate module selection')
        seen.add(module['module_id'])
        ids = module['input_artifact_ids']
        if type(ids) is not list or len(ids) != len(set(opaque_id(i) for i in ids)):
            raise ContractError('Invalid or duplicate input artifact IDs')
        if type(module['parameters']) is not dict:
            raise ContractError('Module parameters must be an object')
    reused = value['reuse_result_ids']
    if type(reused) is not list or len(reused) != len(set(opaque_id(i) for i in reused)):
        raise ContractError('Invalid or duplicate reused result IDs')
    selection = value['clinical_selection']
    if type(selection) is not dict or not set(selection) <= FACT_IDS:
        raise ContractError('Clinical selection contains unsupported facts')
    for observation_id in selection.values():
        opaque_id(observation_id)
    return deepcopy(value)

def default_manifest(episode_id, modules, mode='synthetic', linkage='synthetic_linked'):
    return validate_manifest({
        'schema_version': '1.0', 'episode_id': episode_id, 'population': 'adult',
        'mode': mode, 'linkage': linkage, 'linkage_evidence_artifact_id': None,
        'modules': modules, 'reuse_result_ids': [], 'clinical_selection': {},
        'protocol_id': None,
    })
