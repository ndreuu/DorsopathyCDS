"""Inspectable local prototype; demo values are explicitly synthetic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

from .contracts import loads_json, validate_request, validate_result
from .orchestration import JobRunner
from .registry import ModuleRegistry
from .storage import ArtifactStore, SqliteRepository


def _load_json(path: Path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Contract document is too large")
    return loads_json(path.read_text(encoding="utf-8"))


def run_demo(store: Path):
    from dorsopathy_modules.demo.complaints import DemoComplaintsProvider
    from dorsopathy_modules.demo.mri import DemoMRIProvider

    repository = SqliteRepository(store)
    artifacts = ArtifactStore(store)
    registry = ModuleRegistry()
    providers = [DemoComplaintsProvider(), DemoMRIProvider()]
    for provider in providers:
        registry.register(provider)
    episode_id = "demo-" + uuid.uuid4().hex[:12]
    repository.create_episode(episode_id)
    fixtures = [
        {"synthetic": True, "pain_intensity": 6, "region": "lower_back", "side": "left"},
        {"synthetic": True, "level": "L4-L5", "area_mm2": 104.0},
    ]
    runner = JobRunner(repository, artifacts, registry)
    job_ids = []
    for index, (provider, fixture) in enumerate(zip(providers, fixtures)):
        reference = artifacts.add_bytes(json.dumps(fixture, sort_keys=True).encode("utf-8"), "application/json")
        job_id = episode_id + ".job-" + str(index + 1)
        runner.submit({
            "schema_version": "1.0", "job_id": job_id, "episode_id": episode_id,
            "module_id": provider.spec.module_id, "module_version": provider.spec.module_version,
            "input_artifacts": [reference], "parameters": {}, "protocol_id": "synthetic-static-v1",
        })
        job_ids.append(job_id)
    runner.execute_many(job_ids, max_workers=2)
    snapshot = repository.create_snapshot(episode_id)
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local module platform research scaffold")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run two independent synthetic providers")
    demo.add_argument("--store", type=Path, default=Path("var/platform-demo"))
    demo.add_argument("--json", action="store_true")
    schemas = commands.add_parser("contracts", help="Export the versioned JSON schemas")
    schemas.add_argument("--output", type=Path)
    validate = commands.add_parser("validate-result", help="Check a provider result against its request")
    validate.add_argument("--request", required=True, type=Path)
    validate.add_argument("--result", required=True, type=Path)
    snapshot = commands.add_parser("inspect-snapshot", help="Read an immutable result snapshot")
    snapshot.add_argument("--store", required=True, type=Path)
    snapshot.add_argument("--id", required=True)
    serve = commands.add_parser('serve',help='Open the local multimodal platform')
    serve.add_argument('--store',type=Path,default=Path('var/multimodal'))
    serve.add_argument('--port',type=int,default=8766)
    serve.add_argument('--mri-config',type=Path)
    serve.add_argument('--mri-pathology-config',type=Path,help='Trusted local SpineNet worker configuration')
    serve.add_argument('--raw-workers-config',type=Path,help='Trusted local raw sensor worker configurations')
    serve.add_argument('--example-catalog',type=Path,help='Trusted local public-example catalog')
    flow = commands.add_parser('run-flow',help='Run the full synthetic example or a persisted manifest')
    flow.add_argument('--store',type=Path,default=Path('var/multimodal'))
    flow.add_argument('--manifest',type=Path)
    flow.add_argument('--idempotency-key')
    flow.add_argument('--mri-config',type=Path)
    flow.add_argument('--mri-pathology-config',type=Path,help='Trusted local SpineNet worker configuration')
    flow.add_argument('--raw-workers-config',type=Path,help='Trusted local raw sensor worker configurations')
    flow.add_argument('--output',type=Path)
    flow.add_argument('--timeout',type=float,default=600)
    bundle = commands.add_parser('import-bundle',help='Import and run a locally normalized MRI replay')
    bundle.add_argument('directory',type=Path)
    bundle.add_argument('--store',type=Path,default=Path('var/multimodal'))
    bundle.add_argument('--timeout',type=float,default=600)
    args = parser.parse_args(argv)
    try:
        if args.command == 'serve':
            from .bootstrap import create_application
            from .http import create_server
            app=create_application(args.store,args.mri_config,pathology_config=args.mri_pathology_config,
                                   raw_workers_config=args.raw_workers_config)
            server=None
            try:
                server=create_server(app,port=args.port,example_catalog=args.example_catalog)
                print('DorsopathyCDS modules: http://127.0.0.1:'+str(server.server_port)+'/',flush=True)
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                if server is not None:server.server_close()
                app.close()
        elif args.command == 'import-bundle':
            from .bootstrap import create_application
            from .bundle_import import start_bundle
            from .workflow import wait_for_run
            app=create_application(args.store)
            try:
                run=start_bundle(app,args.directory)
                finished=wait_for_run(app,run['run_id'],timeout_seconds=args.timeout)
                print(json.dumps(finished,ensure_ascii=False,indent=2))
            finally:app.close()
        elif args.command == 'run-flow':
            from .bootstrap import create_application
            from .demo_flow import start_demo
            from .workflow import wait_for_run
            app=create_application(args.store,args.mri_config,pathology_config=args.mri_pathology_config,
                                   raw_workers_config=args.raw_workers_config)
            try:
                if args.manifest:
                    manifest=_load_json(args.manifest)
                    run=app.start_run(manifest,args.idempotency_key or 'cli-'+uuid.uuid4().hex)
                else:
                    run=start_demo(app)
                finished=wait_for_run(app,run['run_id'],timeout_seconds=args.timeout)
                report=app.get_report(run['run_id'])
                if args.output:
                    args.output.parent.mkdir(parents=True,exist_ok=True)
                    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print(json.dumps({'run_id':run['run_id'],'status':finished['status'],
                                  'snapshot_id':finished['snapshot_id'],
                                  'modules':len(app.get_snapshot(finished['snapshot_id'])['results']),
                                  'report_artifact_id':finished['report_artifact']['artifact_id'],
                                  'output':str(args.output) if args.output else None},ensure_ascii=False,indent=2))
            finally:app.close()
        elif args.command == "demo":
            report = run_demo(args.store)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print("Демонстрация платформы: только синтетические значения.")
                print("Эпизод: " + report["episode_id"])
                for result in report["results"]:
                    print(result["module_id"] + " " + result["module_version"] + ": " + result["status"])
                    for observation in result["observations"]:
                        print("  " + observation["concept"] + " = " + str(observation["value"]) + " " + (observation["unit"] or ""))
                print("Сохранён неизменяемый snapshot: " + report["snapshot_id"])
                print("Хранилище: " + str(args.store.resolve()))
        elif args.command == "contracts":
            schema_root = Path(__file__).parent / "schemas/v1"
            result = {path.name: _load_json(path) for path in sorted(schema_root.glob("*.schema.json"))}
            if not result:
                raise ValueError("Schema package is missing")
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                for name, content in result.items():
                    (args.output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(str(args.output))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "validate-result":
            request = validate_request(_load_json(args.request))
            validate_result(_load_json(args.result), request)
            print("Provider request/result satisfy contract 1.0.")
        else:
            result = SqliteRepository(args.store).get_snapshot(args.id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        print("Unable to process platform request: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
