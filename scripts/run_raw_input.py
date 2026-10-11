#!/usr/bin/env python3
"""Persist a source manifest and raw files through the shared application.

Paths and worker commands are trusted CLI configuration, never analysis input.
Each public case receives an isolated episode; no patient linkage is invented.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dorsopathy_platform.api_contracts import canonical_bytes, default_manifest
from dorsopathy_platform.bootstrap import create_application
from dorsopathy_platform.contracts import loads_json
from dorsopathy_platform.workflow import wait_for_run
from dorsopathy_cds.episode_report import render_html


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module',required=True,choices=['complaints','emg.surface','thermography','ultrasound','lidar.posture','nociception'])
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--artifact',action='append',nargs=3,default=[],metavar=('BIND_FIELD','MEDIA_TYPE','PATH'))
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--raw-workers-config',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--synthetic',action='store_true',help='Only for explicitly synthetic source fixtures')
    parser.add_argument('--timeout',type=float,default=1800)
    args=parser.parse_args(argv)
    with args.source.open('rb') as stream:content=stream.read(65537)
    if len(content)>65536:parser.error('Source manifest exceeds 64 KiB')
    source=loads_json(content)
    if type(source) is not dict:parser.error('Source manifest must be an object')
    fields=[item[0] for item in args.artifact]
    if len(set(fields))!=len(fields) or any(field not in source or type(source[field]) is not str for field in fields):
        parser.error('Artifact bindings must match unique top-level source identifier fields')
    app=create_application(args.store,raw_workers_config=args.raw_workers_config)
    try:
        episode=app.create_episode();episode_id=episode['episode_id'];refs=[]
        for field,media_type,filename in args.artifact:
            path=Path(filename)
            with path.open('rb') as stream:ref=app.upload(episode_id,stream,media_type,path.stat().st_size)
            source[field]=ref['artifact_id'];refs.append(ref)
        raw=canonical_bytes(source)
        ref=app.upload(episode_id,BytesIO(raw),'application/json',len(raw));refs.insert(0,ref)
        module={'module_id':args.module,'module_version':'0.2.0','input_artifact_ids':[r['artifact_id'] for r in refs],'parameters':{}}
        manifest=default_manifest(episode_id,[module],mode='synthetic' if args.synthetic else 'live',linkage='synthetic_linked' if args.synthetic else 'unlinked')
        manifest['protocol_id']=source.get('protocol_id')
        run=app.start_run(manifest,'raw-case-'+episode_id)
        run=wait_for_run(app,run['run_id'],timeout_seconds=args.timeout)
        report=app.get_report(run['run_id']);snapshot=app.get_snapshot(run['snapshot_id'])
        args.output.mkdir(parents=True,exist_ok=True)
        for name,value in [('run',run),('source',source),('manifest',manifest),('snapshot',snapshot),('report',report)]:
            (args.output/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        (args.output/'report.html').write_text(render_html(report),encoding='utf-8')
        print(json.dumps({'run_id':run['run_id'],'episode_id':episode_id,'status':run['status'],
                          'module':args.module,'report_schema':report['schema_version'],
                          'screening_status':report['module_report']['results'][0]['status']},ensure_ascii=False))
        return 2 if run['status']=='failed' else 0
    finally:app.close()


if __name__=='__main__':raise SystemExit(main())
