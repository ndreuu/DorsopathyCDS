"""Synthetic sources run through the registered measurement providers."""
import base64
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import uuid

from .api_contracts import canonical_bytes, default_manifest
from .contracts import loads_json

_ASSETS=Path(__file__).parent/'demo_assets'

def demo_inputs():
    from dorsopathy_modules.mri_anatomy.synthetic import synthetic_source
    entries=[]
    layouts=[('complaints','complaints',None,None),('clinical.exam','clinical_exam',None,None),
             ('lidar.posture','lidar','triangle.ply','geometry_artifact_id'),
             ('thermography','thermography',None,None),
             ('emg.surface','emg_surface','signal.csv','csv_artifact_id'),
             ('ultrasound','ultrasound','frame.png','frame_artifact_id'),
             ('nociception','nociception',None,None)]
    media={'triangle.ply':'application/octet-stream','signal.csv':'text/csv','frame.png':'image/png'}
    for module,folder,file,binding in layouts:
        source=loads_json((_ASSETS/folder/'source.json').read_bytes())
        if module=='complaints': source['answers']['pain_intensity']['value']=6
        if module=='clinical.exam':
            source['facts']['imaging.study_status']={'assessment_status':'not_assessed','value':None}
        files=[] if file is None else [{'key':'input','media_type':media[file],
                  'data_base64':base64.b64encode((_ASSETS/folder/file).read_bytes()).decode('ascii')}]
        entries.append({'module_id':module,'sources':[source],'files':files,
                        'bindings':{} if binding is None else {binding:'input'},'parameters':{}})
    entries.insert(2,{'module_id':'mri.anatomy','sources':[synthetic_source()],
                      'files':[],'bindings':{},'parameters':{'processing_mode':'replay'}})
    return deepcopy(entries)

def start_demo(application):
    episode=application.create_episode()['episode_id']
    modules=[]
    for entry in demo_inputs():
        binary_ids=[]; bindings={}; source_ids=[]
        for file in entry['files']:
            data=base64.b64decode(file['data_base64'],validate=True)
            ref=application.upload(episode,BytesIO(data),file['media_type'],len(data))
            bindings[file['key']]=ref['artifact_id']; binary_ids.append(ref['artifact_id'])
        for original in entry['sources']:
            source=deepcopy(original)
            for field,key in entry['bindings'].items():source[field]=bindings[key]
            data=canonical_bytes(source)
            source_ids.append(application.upload(episode,BytesIO(data),'application/json',len(data))['artifact_id'])
        modules.append({'module_id':entry['module_id'],'module_version':'0.1.0',
                        'input_artifact_ids':source_ids+binary_ids,'parameters':entry['parameters']})
    return application.start_run(default_manifest(episode,modules),'demo-'+uuid.uuid4().hex)

def start_review_demo(application,run_id):
    """New synthetic clinician assessment, reusing the exact earlier MRI result."""
    run=application.get_run(run_id)
    if run['status']=='running' or run['manifest']['mode']!='synthetic':
        raise ValueError('A completed synthetic example is required')
    snapshot=application.get_snapshot(run['snapshot_id'])
    mri=next(result for result in snapshot['results'] if result['module_id']=='mri.anatomy')
    source=next(entry for entry in demo_inputs() if entry['module_id']=='clinical.exam')['sources'][0]
    source['assessment_ref']='synthetic-review-'+uuid.uuid4().hex
    source['facts']['imaging.study_status']={'assessment_status':'assessed','value':'performed_result_available'}
    source['facts']['clinical.imaging_clinical_concordance']={
        'assessment_status':'assessed','value':True,'reviewed_result_id':mri['result_id']}
    ids=[]
    for value in [source,mri]:
        data=canonical_bytes(value)
        ids.append(application.upload(run['episode_id'],BytesIO(data),'application/json',len(data))['artifact_id'])
    manifest=default_manifest(run['episode_id'],[{'module_id':'clinical.exam','module_version':'0.1.0',
        'input_artifact_ids':ids,'parameters':{}}])
    manifest['reuse_result_ids']=[r['result_id'] for r in snapshot['results'] if r['module_id']!='clinical.exam']
    return application.start_run(manifest,'review-'+uuid.uuid4().hex)
