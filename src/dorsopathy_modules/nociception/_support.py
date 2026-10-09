"""Small stdlib helpers private to this source-profile provider."""
from datetime import datetime
import json
import math
import struct
import zlib
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, loads_json, validate_request
from dorsopathy_platform.results import observation_id_for

COMMON_FIELDS = {'schema_version','source_kind','acquisition_mode','processing_mode','origin',
 'subject_ref','session_ref','protocol_id','acquired_at','clock_domain','pose',
 'coordinate_frame','axes','anatomy','calibration'}
MAX_SOURCE_BYTES = 8*1024*1024

def require(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ContractError('Source requires exactly documented fields')
    return value

def number(value, positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or positive and value <= 0:
        raise ContractError('Source requires finite numbers in the supported range')
    return float(value)

def text(value):
    if type(value) is not str or not value.strip():
        raise ContractError('Source requires nonempty text')
    return value

def read_source(request, context, spec):
    request=validate_request(request)
    if (request['module_id'],request['module_version']) != (spec.module_id,spec.module_version) or request['parameters']:
        raise ContractError('Source profile requires its exact provider and no parameters')
    sources=[ref for ref in request['input_artifacts'] if ref['media_type']=='application/json' and ref['role']=='source']
    if len(sources)!=1 or sources[0]['size_bytes']>MAX_SOURCE_BYTES:
        raise ContractError('Source profile requires one bounded JSON manifest')
    with context.artifacts.open(sources[0]) as stream:
        raw=stream.read(MAX_SOURCE_BYTES+1)
    if len(raw)>MAX_SOURCE_BYTES:
        raise ContractError('Source profile exceeds supported size')
    return request,loads_json(raw),sources[0]

def common(data, kind, dimensions):
    if data['schema_version']!='1.0' or data['source_kind']!=kind:
        raise ContractError('Unsupported source profile')
    for key in ('acquisition_mode','processing_mode','origin'): text(data[key])
    if (data['acquisition_mode'],data['origin']) not in {('synthetic','synthetic_demo'),('recorded','automatically_measured')} or data['processing_mode']!='fresh':
        raise ContractError('Source acquisition, origin or processing mode is unsupported')
    for key in ('subject_ref','session_ref','protocol_id','clock_domain'):
        if data[key] is not None: opaque_id(data[key])
    for key in ('pose','coordinate_frame'):
        if data[key] is not None: text(data[key])
    axes=data['axes']
    if axes is not None:
        if type(axes) is not list or len(axes)!=dimensions: raise ContractError('Source axes are unsupported')
        for axis in axes: text(axis)
    stamp=data['acquired_at']
    if stamp is not None:
        text(stamp)
        try: parsed=datetime.fromisoformat(stamp[:-1]+'+00:00' if stamp.endswith('Z') else stamp)
        except ValueError: raise ContractError('Invalid acquisition timestamp') from None
        if parsed.tzinfo is None or parsed.utcoffset() is None: raise ContractError('Source time requires known timezone')
    require(data['anatomy'],{'region','side','level','numbering_status'})
    text(data['anatomy']['region'])
    text(data['anatomy']['side'])
    if data['anatomy']['side'] not in {'left','right','bilateral','midline','unspecified'}: raise ContractError('Invalid anatomical side')
    if data['anatomy']['level'] is not None: text(data['anatomy']['level'])
    text(data['anatomy']['numbering_status'])
    if data['anatomy']['numbering_status'] not in {'confirmed','needs_confirmation','not_applicable'}: raise ContractError('Invalid numbering status')

def calibration(data, extra=()):
    item=require(data['calibration'],{'status','source','author_id'}|set(extra))
    text(item['status'])
    if item['status'] not in {'verified','unknown','invalid'}: raise ContractError('Unsupported calibration status')
    for field in ('source','author_id'):
        if item[field] is not None:
            (opaque_id if field=='author_id' else text)(item[field])
    if item['status']=='verified' and (item['source'] is None or item['author_id'] is None):
        raise ContractError('Known calibration requires declared source and author')
    return item

def author(value):
    require(value,{'id','role'}); opaque_id(value['id'])
    if value['role'] not in {'operator','clinician','researcher'}: raise ContractError('Manual annotation requires supported author role')

def add_json(context, data):
    return context.artifacts.add_bytes(json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode(), 'application/json',role='derived')

def metadata(data, source, units, calibration_status):
    return {'schema_version':'1.0','subject_ref':data['subject_ref'],'session_ref':data['session_ref'],
      'protocol_id':data['protocol_id'],'linkage_evidence_refs':[],
      'acquired_interval':{'start':data['acquired_at'],'end':data['acquired_at'],'clock_domain':data['clock_domain']},
      'event_id':None,'pose_id':data['pose'],'calibration_status':calibration_status,
      'calibration_evidence_refs':[{'artifact_id':source['artifact_id'],'selector':{'kind':'json','pointer':'/calibration'}}] if calibration_status=='verified' else [],
      'frame':{'id':data['coordinate_frame'],'units':units,'axes':data['axes']},'transform_refs':[]}

def observation(request,data,concept,instance,value,unit,quality,method,evidence,side=None):
    return {'observation_id':observation_id_for(request['job_id'],concept,instance),
      'concept':concept,'assessment_status':'assessed' if value is not None else 'not_assessed',
      'value':value,'unit':unit,'anatomy':{**data['anatomy'],**({'side':side} if side else {})},
      'quality':quality,'origin':data['origin'],'method':method,'evidence_refs':evidence,
      'model':None,'acquired_at':data['acquired_at'],'coordinate_frame':data['coordinate_frame']}

def png(width,height,rows):
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    raw=b''.join(b'\x00'+bytes(row) for row in rows)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def line(rows,a,b,color=(0,255,255)):
    height=len(rows);width=len(rows[0])//3
    x0,y0=(int(round(a[0])),int(round(a[1])));x1,y1=(int(round(b[0])),int(round(b[1])))
    dx=abs(x1-x0);sx=1 if x0<x1 else -1;dy=-abs(y1-y0);sy=1 if y0<y1 else -1;error=dx+dy
    while True:
        if 0<=x0<width and 0<=y0<height: rows[y0][x0*3:x0*3+3]=bytes(color)
        if x0==x1 and y0==y1: break
        doubled=2*error
        if doubled>=dy: error+=dy;x0+=sx
        if doubled<=dx: error+=dx;y0+=sy
