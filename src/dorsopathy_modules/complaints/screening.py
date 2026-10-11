"""Patient answers for screening; no numeric severity threshold or diagnosis."""
from dorsopathy_platform.contracts import ContractError, loads_json, validate_request, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import observation_id_for, result_envelope
from .provider import _object, _id, _answer, _FIELDS, _MAX_SOURCE_BYTES, _SIDES, _STATES

BOOLEAN_ANSWERS = {
    'severe_low_back_pain': 'symptoms.low_back_pain.severe',
    'leg_radiation': 'symptoms.pain.leg_radiation',
    'new_bladder_disturbance': 'symptoms.new_bladder_disturbance',
    'new_bowel_disturbance': 'symptoms.new_bowel_disturbance',
    'new_sexual_disturbance': 'symptoms.new_sexual_disturbance',
    'new_perineal_numbness': 'symptoms.new_perineal_numbness',
}


def validate_source(data):
    _object(data, _FIELDS)
    if data['schema_version'] != '1.0' or data['source_kind'] != 'complaints.screening.v2':
        raise ContractError('Unsupported screening questionnaire')
    if type(data['mode']) is not str or type(data['origin']) is not str or (data['mode'], data['origin']) not in {
        ('synthetic_demo', 'synthetic_demo'), ('patient_input', 'patient_report')
    }:
        raise ContractError('Screening origin mismatch')
    for field in ('form_id', 'form_version', 'assessment_ref'):
        _id(data[field])
    _object(data['author'], {'id', 'role'}); _id(data['author']['id'])
    if data['author']['role'] != 'patient':
        raise ContractError('Screening answers require patient origin')
    _object(data['anatomy'], {'region', 'side'})
    if data['anatomy']['region'] != 'lower_back' or type(data['anatomy']['side']) is not str or data['anatomy']['side'] not in _SIDES:
        raise ContractError('Unsupported screening anatomy')
    _object(data['answers'], set(BOOLEAN_ANSWERS) | {'pain_intensity', 'duration'})
    _answer(data['answers']['pain_intensity'], '{score}', intensity=True)
    _answer(data['answers']['duration'], 'd')
    for key in BOOLEAN_ANSWERS:
        value=data['answers'][key];_object(value, {'assessment_status', 'value', 'unit'})
        status=value['assessment_status']
        if type(status) is not str or status not in _STATES or value['unit'] is not None:
            raise ContractError('Unsupported screening answer status or unit')
        if status == 'assessed':
            if type(value['value']) is not bool:
                raise ContractError('Screening answer must be an explicit boolean')
        elif value['value'] is not None:
            raise ContractError('Unknown screening answer must be null')
    _object(data['bodymap'], {'assessment_status', 'selected_regions'})
    status=data['bodymap']['assessment_status'];regions=data['bodymap']['selected_regions']
    if type(status) is not str or status not in _STATES:
        raise ContractError('Unsupported body map status')
    if status == 'assessed':
        if type(regions) is not list or len(regions)>100:
            raise ContractError('Body map requires bounded regions')
        for region in regions:_id(region)
        if len(set(regions)) != len(regions):raise ContractError('Duplicate body map regions')
    elif regions is not None:raise ContractError('Unknown body map must be null')
    return data


class ScreeningComplaintsProvider:
    spec=ProviderSpec('complaints', '0.2.0', 'Жалобы и настораживающие симптомы')

    def analyze(self,request,context):
        request=validate_request(request)
        if (request['module_id'],request['module_version']) != ('complaints','0.2.0') or request['parameters'] or len(request['input_artifacts']) != 1:
            raise ContractError('Screening requires one source and no parameters')
        source=request['input_artifacts'][0]
        if source['role'] != 'source' or source['media_type'] != 'application/json' or source['size_bytes']>_MAX_SOURCE_BYTES:
            raise ContractError('Screening requires bounded source JSON')
        with context.artifacts.open(source) as stream:raw=stream.read(_MAX_SOURCE_BYTES+1)
        if len(raw)>_MAX_SOURCE_BYTES:raise ContractError('Screening source exceeds limit')
        data=validate_source(loads_json(raw))
        concepts={'pain_intensity':'symptoms.pain_intensity','duration':'symptoms.pain_duration',**BOOLEAN_ANSWERS}
        observations=[]
        for key,concept in concepts.items():
            answer=data['answers'][key]
            observations.append({
                'observation_id':observation_id_for(request['job_id'],concept),
                'concept':concept,'assessment_status':answer['assessment_status'],
                'value':answer['value'],'unit':answer['unit'],
                'anatomy':{**data['anatomy'],'level':None,'numbering_status':'not_applicable'},
                'quality':'usable' if answer['assessment_status']=='assessed' else 'unknown',
                'origin':data['origin'],'method':'structured-patient-screening-v2',
                'acquired_at':data['acquired_at'],'coordinate_frame':None,
                'evidence_refs':[{'artifact_id':source['artifact_id'],'selector':{'kind':'json','pointer':'/answers/'+key}}],
                'model':None,
            })
        partial=any(a['assessment_status']=='not_assessed' for a in data['answers'].values()) or data['bodymap']['assessment_status']=='not_assessed'
        warnings=[{'code':'incomplete_screening','message':'Missing answers remain unknown.'}] if partial else []
        return validate_result(result_envelope(request,observations,[source],'partial' if partial else 'complete',warnings),request)
