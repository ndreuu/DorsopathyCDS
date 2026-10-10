#!/usr/bin/env python3
"""Upload explicitly selected public input profiles to the local platform.
Actual data and this script's runtime index/catalog stay outside Git.
"""
import argparse
import hashlib
import http.client
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from dorsopathy_platform.api_contracts import default_manifest

class LocalAPI:
    def __init__(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path not in {'','/'} or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Only the local platform HTTP URL is supported')
        self.host=parsed.hostname;self.port=parsed.port or 8766
    def request(self,method,path,value=None,headers=None,file=None,media=None):
        conn=http.client.HTTPConnection(self.host,self.port,timeout=60)
        try:
            if file is not None:
                size=file.stat().st_size
                conn.putrequest(method,path);conn.putheader('Content-Type',media);conn.putheader('Content-Length',str(size));conn.endheaders()
                with file.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):conn.send(chunk)
            else:
                body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if value is not None else None
                conn.request(method,path,body,{'Content-Type':'application/json',**(headers or {})})
            response=conn.getresponse();raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024 or not 200<=response.status<300:
                raise RuntimeError('Local API returned '+str(response.status))
            return json.loads(raw)
        finally:conn.close()

def sha_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True)
    parser.add_argument('--base-url',default='http://127.0.0.1:8766')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=600,help='Maximum wait per run in seconds')
    parser.add_argument('--append',action='store_true',help='Retain existing examples and add distinct new cases')
    args=parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0:raise ValueError('Timeout must be positive and finite')
    index=json.loads(args.index.read_text())
    api=LocalAPI(args.base_url);catalog={'schema_version':'1.0','examples':[],'unavailable':index.get('unavailable',[])}
    if args.append:
        from dorsopathy_platform.real_examples import read_catalog
        catalog=read_catalog(args.output)
        catalog['unavailable']=index.get('unavailable',catalog['unavailable'])
        if {e['id'] for e in catalog['examples']}&{e['id'] for e in index['examples']}:
            raise ValueError('Append entries must have new IDs')
    known_modules={(entry['module_id'],entry['module_version']) for entry in api.request('GET','/modules')}
    for entry in index['examples']:
        if (entry['module_id'],'0.1.0') not in known_modules:raise ValueError('Unknown module')
        episode=api.request('POST','/episodes',{})['episode_id'];ids=[]
        for file in entry['inputs']:
            path=Path(file['path']);expected=file.get('sha256') or sha_file(path)
            if path.is_symlink() or sha_file(path)!=expected:raise ValueError('Input hash mismatch')
            ref=api.request('POST','/episodes/'+episode+'/artifacts',file=path,media=file['media_type'])
            if ref['sha256']!=expected:raise ValueError('Upload content changed')
            ids.append(ref['artifact_id'])
        manifest=default_manifest(episode,[{'module_id':entry['module_id'],'module_version':'0.1.0','input_artifact_ids':ids,'parameters':entry.get('parameters',{})}],mode=entry.get('mode','replay'),linkage='unlinked')
        run=api.request('POST','/episodes/'+episode+'/runs',manifest,{'Idempotency-Key':'real-'+uuid.uuid4().hex})
        deadline=time.monotonic()+args.timeout
        while run['status']=='running':
            if time.monotonic()>deadline:raise TimeoutError('Example timed out; existing run is retained')
            time.sleep(.2);run=api.request('GET','/runs/'+run['run_id'])
        if not run.get('snapshot_id'):raise RuntimeError('Run did not produce a snapshot')
        snapshot=api.request('GET','/snapshots/'+run['snapshot_id'])
        result=next(r for r in snapshot['results'] if r['module_id']==entry['module_id'])
        if result['status']=='failed':raise RuntimeError('Real provider failed: '+entry['module_id'])
        pngs=[a['artifact_id'] for a in result['artifacts'] if a['media_type']=='image/png']
        selected=entry.get('preview_artifact_ids')
        if selected is None:
            positions=entry.get('preview_indices',[0]);selected=[pngs[i] for i in positions if type(i) is int and 0<=i<len(pngs)]
        if type(selected) is not list or len(selected)>6 or any(aid not in pngs for aid in selected):
            raise ValueError('Previews must be PNG artifacts of this result')
        item={k:entry[k] for k in ('id','title','description','module_id','source','notes')}
        item.update(episode_id=episode,run_id=run['run_id'],preview_artifact_ids=selected)
        catalog['examples'].append(item)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        temporary=args.output.with_suffix('.tmp');temporary.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');temporary.replace(args.output)
        print(entry['id']+': '+result['status']+' '+args.base_url.rstrip('/')+'/#run='+run['run_id'],flush=True)
    print('Catalog: '+str(args.output),flush=True)

if __name__=='__main__':main()
