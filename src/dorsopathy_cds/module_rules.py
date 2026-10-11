"""Source-bound module-local screening and clinical-use routing.

Sensor measurements do not become diagnoses by undocumented thresholds.
Only validated questionnaire answers feed the NICE referral criterion.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_platform.comparison import _read_json, _validated_inputs
from dorsopathy_modules.complaints.screening import BOOLEAN_ANSWERS, validate_source
from .engine import evaluate, validate_rules

CATALOG_ROOT=Path(__file__).parent/'catalog'


def load_module_catalog():
    rules=json.loads((CATALOG_ROOT/'module_rules.json').read_text(encoding='utf-8'))
    sources=json.loads((CATALOG_ROOT/'module_sources.json').read_text(encoding='utf-8'))
    validate_rules(rules,sources)
    return {'rules':rules,'sources':sources}


def _candidate(result, observation, artifacts):
    entry={'result_id':result['result_id'],'observation_id':observation['observation_id'],
           'assessment_status':observation['assessment_status'],'origin':observation['origin'],
           'value':None,'source_artifact_id':None,'source_sha256':None,
           'source_selector':None,'reason_codes':[]}
    if (result['module_id'],result['module_version']) != ('complaints','0.2.0') or result['status']=='failed':
        entry['reason_codes']=['unsupported_screening_producer'];return entry
    if observation['origin'] not in {'patient_report','synthetic_demo'} or observation['method']!='structured-patient-screening-v2' or observation['model'] is not None or observation['unit'] is not None:
        entry['reason_codes']=['screening_origin_or_method_mismatch'];return entry
    key=next(k for k,c in BOOLEAN_ANSWERS.items() if c==observation['concept'])
    selector={'kind':'json','pointer':'/answers/'+key}
    evidence=[e for e in observation['evidence_refs'] if e['selector']==selector]
    if len(evidence)!=1:
        entry['reason_codes']=['exact_questionnaire_evidence_missing'];return entry
    refs={a['artifact_id']:a for a in result['artifacts']}
    try:
        ref=refs[evidence[0]['artifact_id']]
        if ref['role']!='source':raise ValueError('Not source')
        source=validate_source(_read_json(ref,artifacts));answer=source['answers'][key]
        entry.update(source_artifact_id=ref['artifact_id'],source_sha256=ref['sha256'],source_selector=selector)
        if source['origin']!=observation['origin'] or source['acquired_at']!=observation['acquired_at'] or answer['assessment_status']!=observation['assessment_status'] or canonical_bytes(answer['value'])!=canonical_bytes(observation['value']):
            entry['reason_codes']=['questionnaire_observation_source_mismatch']
        elif observation['assessment_status']!='assessed':
            entry['reason_codes']=[observation['assessment_status']]
        elif observation['quality']!='usable':
            entry['reason_codes']=['questionnaire_quality_unusable']
        else:entry['value']=answer['value']
    except (ValueError,OSError,KeyError,TypeError):
        entry['reason_codes']=['questionnaire_source_invalid_or_unavailable']
    return entry


def evaluate_modules(snapshot,manifest,artifacts):
    snapshot,manifest,results,_=_validated_inputs(snapshot,manifest)
    catalog=load_module_catalog()
    concepts=set(BOOLEAN_ANSWERS.values());candidates={c:[] for c in concepts}
    for result in sorted(results,key=lambda r:r['result_id']):
        for observation in result['observations']:
            if observation['concept'] in concepts:
                candidates[observation['concept']].append(_candidate(result,observation,artifacts))
    # A single questionnaire can drive its own referral check. Multimodal
    # linkage is not needed for this local rule, but mixed demo/real sources
    # must not produce a clinical interpretation of a real episode.
    origins={o['origin'] for r in results for o in r['observations']}
    mixed='synthetic_demo' in origins and bool(origins-{'synthetic_demo'})
    mode_mismatch=(manifest['mode']=='synthetic' and bool(origins-{'synthetic_demo'})) or (manifest['mode']!='synthetic' and 'synthetic_demo' in origins)
    lineage={};facts={}
    for concept in sorted(concepts):
        values=candidates[concept]
        accepted=values[0] if len(values)==1 and not values[0]['reason_codes'] and not mixed and not mode_mismatch else None
        reasons=sorted({reason for v in values for reason in v['reason_codes']})
        if len(values)>1:reasons.append('multiple_questionnaires_require_separate_episode')
        if not values:reasons.append('screening_answer_missing')
        if mixed:reasons.append('mixed_synthetic_real_origins')
        if mode_mismatch:reasons.append('screening_mode_origin_mismatch')
        facts[concept]=accepted['value'] if accepted is not None else None
        lineage[concept]={'accepted':deepcopy(accepted),'candidates':deepcopy(values),'reason_codes':sorted(set(reasons))}
    checks=[]
    for rule in catalog['rules']:
        check=evaluate(rule,facts,catalog['sources'])
        check.update(title=rule['title'],clinical_review_status=rule['clinical_review_status'],
                     formalization_status=rule['formalization_status'],diagnostic_output=rule['diagnostic_output'],
                     mapping_notes=deepcopy(rule['mapping_notes']),scope='questionnaire_only')
        check['interpretation']={
            'met':'Условия скринингового правила выполнены: срочная оценка по местному маршруту для подозрения на синдром конского хвоста. Это не установленный диагноз.',
            'not_met':'Условия этого правила не выполнены. Синдром конского хвоста и другие причины боли не исключены.',
            'indeterminate':'Для проверки срочного направления недостаточно достоверных ответов пациента.',
        }[check['status']]
        check['action']='immediate_local_assessment' if check['status']=='met' else None
        index={(d['id'],q['id']):dict(q,doc_id=d['id'],quote_id=q['id']) for d in catalog['sources']['documents'] for q in d['quotes']}
        check['action_citations']=[deepcopy(index[(c['doc_id'],c['quote_id'])]) for c in rule['action_citations']]
        checks.append(check)
    uses=[]
    for result in sorted(results,key=lambda r:(r['module_id'],r['result_id'])):
        module=result['module_id']
        if module=='complaints' and result['module_version']=='0.2.0':
            role='source_bound_referral_screening';reason='Явные ответы пациента проверяются по NG127 1.7.3.';source_ids=['nice_ng127']
        elif module=='clinical.exam':
            role='clinician_assessment';reason='Оценки врача участвуют в диагностической триаде, с проверкой происхождения и связи исследований.';source_ids=['777_1']
        elif module.startswith('mri.'):
            role='structural_candidate_for_clinical_review';reason='МРТ-находки доступны для сопоставления с клиникой; автоматического подтверждения причин боли нет.';source_ids=['777_1']
        elif module=='emg.surface':
            role='functional_research_measurement';reason='Рассчитываются сигнальные показатели; проверенного диагностического правила для радикулопатии по этим признакам в MVP нет.';source_ids=['aanem_surface']
        elif module=='nociception':
            role='physiological_research_measurement';reason='Разрешённое применение PMD-200 относится к взрослым под общей анестезией; профиль дорсопатии не подтверждён этими измерениями.';source_ids=['fda_pmd200']
        else:
            role='research_measurement_without_validated_diagnostic_rule';reason='Измерения и их качество доступны для исследования; клинический диагностический порог для этих признаков в MVP не задан.';source_ids=[]
        uses.append({'result_id':result['result_id'],'module_id':module,'module_version':result['module_version'],
                     'role':role,'reason':reason,'source_document_ids':source_ids,
                     'assessed_count':sum(o['assessment_status']=='assessed' for o in result['observations']),
                     'unknown_count':sum(o['assessment_status']=='not_assessed' for o in result['observations']),
                     # Keep all scalar facts, but freeze heavy evidence/model metadata
                     # once per result. Exact complete observations remain in the
                     # immutable snapshot, identified here by its result digest.
                     'observations':[{k:deepcopy(o[k]) for k in ('observation_id','concept',
                         'assessment_status','value','unit','anatomy','quality','origin')}
                         for o in result['observations']],
                     'result_sha256':hashlib.sha256(canonical_bytes(result)).hexdigest(),
                     'evidence_artifacts':deepcopy(result['artifacts']),
                     'methods':sorted({o['method'] for o in result['observations']}),
                     'models':[json.loads(value) for value in sorted({json.dumps(o['model'],sort_keys=True)
                         for o in result['observations'] if o['model'] is not None})],
                     'result_status':result['status']})
    return {'schema_version':'1.0','snapshot_id':snapshot['snapshot_id'],
            'catalog_snapshot':catalog,'catalog_sha256':hashlib.sha256(canonical_bytes(catalog)).hexdigest(),
            'facts':facts,'lineage':lineage,'results':checks,'module_use':uses,
            'disease_exclusion':False,'diagnostic_probability':None,
            'clinical_review_status':'requires_clinical_review'}
