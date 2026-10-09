"""Fresh parent TSS step2 inference, then the same geometry/replay bridge.

Model assets and the parent repository are read-only. Every run has a new
private directory. The subprocess owns its timeout and cleans up its group.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid

from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.process_io import read_worker_request, safe_exchange_path, write_worker_output
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.storage import ArtifactStore
from dorsopathy_modules.mri_anatomy import analyze_source
from dorsopathy_modules.mri_import.bridge import configure_parent, normalize_parent_export


class WorkerArtifacts:
    """Read verified exchange inputs; newly produced images are always derived."""
    def __init__(self,exchange,envelope):
        self.exchange=Path(exchange); self.store=ArtifactStore(self.exchange/"derived")
        self.inputs={ref["artifact_id"]:ref for ref in envelope["request"]["input_artifacts"]}
        self.paths={item["artifact_id"]:safe_exchange_path(exchange,item["relative_path"]) for item in envelope["inputs"]}
        self.refs={}
    def open(self,ref):
        if ref["artifact_id"] in self.inputs:
            if ref!=self.inputs[ref["artifact_id"]]: raise ContractError("Worker source identity mismatch")
            return self.paths[ref["artifact_id"]].open("rb")
        return self.store.open(ref)
    def read(self,ref):
        with self.open(ref) as stream: return stream.read()
    def _record(self,ref):
        if ref["artifact_id"] in self.inputs: return self.inputs[ref["artifact_id"]]
        self.refs[ref["artifact_id"]]=ref; return ref
    def add_file(self,path,media_type,role="source"):
        return self._record(self.store.add_file(path,media_type,"derived"))
    def add_bytes(self,data,media_type,role="source"):
        return self._record(self.store.add_bytes(data,media_type,"derived"))
    def descriptors(self,result):
        return [{"artifact_id":ref["artifact_id"],"relative_path":"derived/"+ref["storage_key"],
                 **{key:ref[key] for key in ("sha256","size_bytes","media_type","role")}}
                for ref in result["artifacts"] if ref["artifact_id"] not in self.inputs]


def _kill_group(process):
    if process is None or getattr(process,"_mri_cleanup_done",False): return
    process._mri_cleanup_done=True
    # A normally exited leader may leave model children. One unconditional group
    # kill avoids macOS TERM/second-KILL races after adopted children exit.
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait(timeout=.5)


def _infer(argv,log_path,timeout):
    process=None
    original={}
    def interrupted(signum,frame):
        if process is not None and not getattr(process,"_mri_cleanup_done",False):
            process._mri_cleanup_done=True
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=.2)
        raise SystemExit(128+signum)
    for number in (signal.SIGTERM,signal.SIGINT):
        original[number]=signal.signal(number,interrupted)
    try:
        env=dict(os.environ)
        env.update(OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",nnUNet_def_n_proc="1")
        with log_path.open("wb") as log:
            process=subprocess.Popen(argv,shell=False,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            try: returncode=process.wait(timeout=timeout)
            except subprocess.TimeoutExpired: raise ContractError("Fresh MRI inference exceeded its configured time limit") from None
            if returncode!=0: raise ContractError("Fresh MRI inference did not exit successfully")
    finally:
        _kill_group(process)
        for number,handler in original.items(): signal.signal(number,handler)


def _import_input(path,run_dir,contrast,plane):
    from spine_backend.imaging import import_images
    with path.open("rb") as stream: header=stream.read(256)
    if header.startswith(b"PK"):
        import asyncio
        from spine_backend.uploads import save_uploads
        class Upload:
            filename="input.zip"
            def __init__(self): self.stream=path.open("rb")
            async def read(self,size): return self.stream.read(size)
            async def close(self): self.stream.close()
        input_path=asyncio.run(save_uploads([Upload()],run_dir/"uploads"))
    else:
        suffix=".mha" if b"ObjectType" in header or b"ElementDataFile" in header else ".nii.gz" if header.startswith(b"\x1f\x8b") else ".nii" if header[:4] in (b"\x5c\x01\0\0",b"\0\0\x01\x5c") else ".dcm"
        input_path=run_dir/("input"+suffix); shutil.copyfile(path,input_path)
    series=import_images(input_path,run_dir/"imported")
    for entry in series:
        if entry["source_format"]!="dicom":
            if contrast is not None:
                entry["contrast"]=contrast; entry["contrast_source"]="trusted-worker-explicit-declaration"
                entry["warnings"].append("Volume contrast was explicitly declared in the trusted worker configuration, not inferred from acquisition metadata.")
            if entry["plane"]=="unknown" and plane is not None:
                entry["plane"]=plane; entry["plane_source"]="trusted-worker-explicit-declaration"
    suitable=[entry for entry in series if entry["contrast"]=="T2" and entry["plane"] in ("sagittal","unknown")]
    if len(suitable)!=1: raise ContractError("Fresh anatomy requires exactly one supported sagittal T2 series")
    return suitable[0]


def fresh_anatomy(source_path,run_dir,args):
    import numpy as np
    import SimpleITK as sitk
    from spine_backend.imaging import read_volume
    from spine_backend.levels import analyze_levels
    from spine_backend.segmentation import TotalSpineSegConfig,_provider_files,_weights_provenance,_validate_segmentation
    series=_import_input(source_path,run_dir,args.contrast,args.plane)
    provider=args.tss_python.parent/"totalspineseg"
    config=TotalSpineSegConfig(command=provider,python_command=args.tss_python,data_dir=args.weights_dir,
                              manifest_path=args.weights_dir/"manifest.json",device="cpu",timeout_seconds=args.timeout_seconds)
    _,files,_=_provider_files(config)
    provenance=_weights_provenance(files,config)  # Actual checkpoint bytes are hashed before inference.
    image=read_volume(Path(series["volume_path"])); array=sitk.GetArrayViewFromImage(image)
    if not np.isfinite(array).all() or np.ptp(array)==0: raise ContractError("MRI source intensities are invalid or constant")
    input_nii=run_dir/"input.nii.gz"; sitk.WriteImage(image,str(input_nii),useCompression=True)
    output=run_dir/"inference"; log=run_dir/"inference.log"
    if args.device=="mps":
        argv=[str(args.tss_python),"-m","dorsopathy_workers.mri.tss_driver","--parent-script",
              str(args.parent_root/"scripts/totalspineseg_mps.py"),"--",str(input_nii),str(output),"--device","mps"]
    else: argv=[str(provider),str(input_nii),str(output),"--device","cpu"]
    argv += ["--data-dir",str(args.weights_dir),"--max-workers","1","--max-workers-nnunet","1","--quiet","--keep-only","step2_output"]
    with source_path.open('rb') as source_stream:
        source_hash=hashlib.file_digest(source_stream,'sha256').hexdigest()
    runtime={"schema_version":"1.0","processing":"fresh","status":"running","started_at":datetime.now(timezone.utc).isoformat(),
             "device":args.device,"timeout_seconds":args.timeout_seconds,"max_workers":1,"weights":provenance,
             "source_input_sha256":source_hash,
             "original_parent_files_modified":False,"existing_outputs_reused":False,
             "shutdown_strategy":"parent-main-returned-zero-then-supervised-os-exit" if args.device=="mps" else "original-cli-exit"}
    runtime_path=run_dir/"fresh-runtime.json"
    def save(): runtime_path.write_text(json.dumps(runtime,indent=2)+"\n")
    save(); started=time.monotonic()
    try:
        _infer(argv,log,args.timeout_seconds)
        prediction=output/"step2_output/input.nii.gz"
        labels,counts=_validate_segmentation(image,prediction)
        mask=run_dir/"segmentation.mha"; sitk.WriteImage(labels,str(mask),useCompression=True)
        anatomy=analyze_levels(Path(series["volume_path"]),mask,run_dir/"levels",series)
        segmentation={"status":"completed","segmentation_path":str(mask),"provenance":provenance,"label_voxel_counts":counts}
        analysis={"schema_version":"lumbar-levels-v1","levels":anatomy["levels"],"segmentation":segmentation}
        runtime.update(status="completed",returncode=0)
        payload={"series":series,"analysis":analysis}
        (run_dir/"parent-export.json").write_text(json.dumps(payload,indent=2)+"\n")
        return payload,provenance,runtime_path
    except BaseException:
        runtime.update(status="failed",reason_code="fresh_inference_or_geometry_failed")
        raise
    finally:
        runtime.update(finished_at=datetime.now(timezone.utc).isoformat(),duration_seconds=time.monotonic()-started)
        save()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange-dir",type=Path,required=True); parser.add_argument("--parent-root",type=Path,required=True)
    parser.add_argument("--tss-python",type=Path,required=True); parser.add_argument("--weights-dir",type=Path,required=True)
    parser.add_argument("--device",choices=("cpu","mps"),default="cpu"); parser.add_argument("--timeout-seconds",type=int,default=180)
    parser.add_argument("--contrast",choices=("T2",)); parser.add_argument("--plane",choices=("sagittal",))
    args=parser.parse_args(argv)
    if not 1<=args.timeout_seconds<=1800: parser.error("timeout must be between 1 and 1800 seconds")
    configure_parent(args.parent_root)
    envelope=read_worker_request(args.exchange_dir); request=envelope["request"]
    artifacts=WorkerArtifacts(args.exchange_dir,envelope); context=ProviderContext(artifacts)
    run_dir=args.exchange_dir/("fresh-mri-"+uuid.uuid4().hex); run_dir.mkdir()
    try:
        if (request["module_id"],request["module_version"])!=("mri.anatomy","0.1.0") or request["parameters"]!={"processing_mode":"live"} or len(request["input_artifacts"])!=1:
            raise ContractError("Live MRI worker requires one volume artifact and processing_mode live")
        source_path=artifacts.paths[request["input_artifacts"][0]["artifact_id"]]
        payload,provenance,runtime_path=fresh_anatomy(source_path,run_dir,args)
        data,linked=normalize_parent_export(payload,context,processing="fresh")
        checkpoints={name:digest for name,digest in provenance["checkpoint_files_sha256"].items() if "step2" in name and name.endswith(".pth")}
        if len(checkpoints)!=1: raise ContractError("Verified step2 checkpoint identity is ambiguous")
        data["model"]={"id":"totalspineseg-step2","version":provenance["weights_release"],"weights_sha256":next(iter(checkpoints.values()))}
        data["provenance"].update(weights_verified=True,all_model_files_sha256=provenance["checkpoint_files_sha256"],
            original_upload_sha256=request["input_artifacts"][0]["sha256"],inference="fresh_external_parent_step2")
        source=artifacts.add_bytes(json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False).encode(),"application/json","derived")
        result=analyze_source(data,request,context,source,linked)
        result["artifacts"].append(artifacts.add_file(runtime_path,"application/json","derived"))
        result=validate_result(result,request)
    except Exception as error:
        audit=artifacts.add_bytes(json.dumps({"schema_version":"1.0","processing":"fresh","status":"failed",
            "reason_code":"mri_worker_failed","error_type":type(error).__name__,"fresh_inference_successful":False}).encode(),"application/json","derived")
        result=result_envelope(request,[],[*request["input_artifacts"],audit],"failed")
        runtime_path=run_dir/"fresh-runtime.json"
        if runtime_path.is_file(): result["artifacts"].append(artifacts.add_file(runtime_path,"application/json","derived"))
        result["error"]={"code":"mri_worker_failed","message":"Fresh MRI processing failed; inspect the private worker log and runtime evidence."}
    write_worker_output(args.exchange_dir,result,artifacts.descriptors(result))
    return 0
