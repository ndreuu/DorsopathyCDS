"""Offline export -> uploadable source-grid bundle, exact linked content files."""
import argparse
import json
from pathlib import Path

from dorsopathy_platform.contracts import loads_json
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_modules.mri_anatomy import analyze_source
from .bridge import bundle_source_arrays, normalize_parent_export, normalize_spinosarc_export


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export",type=Path); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--parent-root",type=Path); parser.add_argument("--spinosarc-root",type=Path)
    args=parser.parse_args(argv)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    store=ArtifactStore(args.output_dir); context=ProviderContext(store)
    payload=loads_json(args.export.read_bytes())
    if {"series","analysis"}<=set(payload): data,refs=normalize_parent_export(payload,context,args.parent_root)
    elif args.spinosarc_root is not None: data,refs=normalize_spinosarc_export(payload,context,args.spinosarc_root)
    else: parser.error("SpinoSarc export requires --spinosarc-root")
    data,array_refs=bundle_source_arrays(data,context)
    refs={ref["artifact_id"]:ref for ref in [*refs,*array_refs]}
    refs=list(refs.values())
    bundle=args.output_dir/"bundle.json"; bundle.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    bundle_ref=store.add_file(bundle,"application/json","source")
    request={"schema_version":"1.0","job_id":"offline-mri-replay","episode_id":"offline-replay-episode",
             "module_id":"mri.anatomy","module_version":"0.1.0","input_artifacts":[bundle_ref,*refs],"parameters":{},"protocol_id":None}
    result=analyze_source(data,request,context,bundle_ref)
    (args.output_dir/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    # The first file is uploaded first; IDs remain content-addressed after upload.
    upload={"schema_version":"1.0","processing":"replay","first_artifact_id":bundle_ref["artifact_id"],
            "files":[{"artifact_id":ref["artifact_id"],"relative_path":ref["storage_key"],"media_type":ref["media_type"],"sha256":ref["sha256"],"size_bytes":ref["size_bytes"]} for ref in request["input_artifacts"]],
            "linkage":"unlinked","clinical_use":"research_only"}
    (args.output_dir/"upload-manifest.json").write_text(json.dumps(upload,ensure_ascii=False,indent=2)+"\n")
    print("Replay bundle and linked evidence saved; no inference was run.")
    return 0


if __name__=="__main__": raise SystemExit(main())
