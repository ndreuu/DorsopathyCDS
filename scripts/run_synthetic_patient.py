#!/usr/bin/env python3
"""Import an explicitly synthetic patient ZIP and run real registered providers."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dorsopathy_cds.episode_report import render_html
from dorsopathy_platform.bootstrap import create_application
from dorsopathy_platform.synthetic_bundle import start_synthetic_bundle
from dorsopathy_platform.workflow import wait_for_run


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--raw-workers-config',type=Path,required=True)
    parser.add_argument('--store',type=Path,default=Path('var/synthetic-patient-demo'))
    parser.add_argument('--output',type=Path,default=Path('var/synthetic-patient-run'))
    parser.add_argument('--timeout',type=float,default=1800)
    args=parser.parse_args(argv)
    app=create_application(args.store,raw_workers_config=args.raw_workers_config)
    try:
        run,bundle=start_synthetic_bundle(app,args.archive)
        run=wait_for_run(app,run['run_id'],timeout_seconds=args.timeout)
        report=app.get_report(run['run_id']);snapshot=app.get_snapshot(run['snapshot_id'])
        modules=[{'module_id':r['module_id'],'module_version':r['module_version'],'status':r['status'],
                  'assessed_count':sum(o['assessment_status']=='assessed' for o in r['observations']),
                  'not_assessed_count':sum(o['assessment_status']=='not_assessed' for o in r['observations'])}
                 for r in snapshot['results']]
        origins=sorted({o['origin'] for r in snapshot['results'] for o in r['observations']})
        failed=[r['module_id'] for r in snapshot['results'] if r['status']=='failed']
        summary={'schema_version':'1.0','case_id':bundle['case']['case_id'],'origin':'synthetic_demo',
                 'run_id':run['run_id'],'episode_id':run['episode_id'],'run_status':run['status'],
                 'modules':modules,'origins':origins,'failed_modules':failed,
                 'rules':[{'id':r['rule_id'],'status':r['status'],'action':r.get('action')} for r in report['module_report']['results']],
                 'clinical_rules':[{'id':r['rule_id'],'status':r['status']} for r in report['clinical_report']['results']],
                 'archive_sha256':bundle['archive']['sha256'],
                 'demo_url':'http://127.0.0.1:8769/#run='+run['run_id'],
                 'archive_url':'http://127.0.0.1:8769/episodes/'+run['episode_id']+'/artifacts/'+bundle['archive']['artifact_id']}
        args.output.mkdir(parents=True,exist_ok=True)
        for name,value in [('run',run),('bundle',bundle),('snapshot',snapshot),('report',report),('summary',summary)]:
            (args.output/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        (args.output/'report.html').write_text(render_html(report),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False))
        return 2 if failed or origins!=['synthetic_demo'] or len(modules)!=8 else 0
    finally:app.close()


if __name__=='__main__':raise SystemExit(main())
