"""Read existing exports, validate linked images/masks, then normalize planes.

Paths in this adapter are trusted offline configuration, never HTTP parameters.
No parent or SpinoSarc output is changed. Replay never invokes inference.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

from dorsopathy_modules.mri_anatomy import analyze_source
from dorsopathy_platform.contracts import ContractError, loads_json


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""): digest.update(chunk)
    return digest.hexdigest()


def bundle_source_arrays(data, context):
    """Externalize inline planes into bounded JSON artifacts for HTTP replay.

    This is serialization only, never remeasurement or model inference.
    Returns (independent normalized_source, array_artifact_refs).
    """
    bundled=deepcopy(data); refs={}
    for frame in bundled["frames"]:
        for key,role in (("image","source"),("mask","derived")):
            if frame[key] is not None:
                payload=json.dumps(frame[key],separators=(",",":"),allow_nan=False).encode()
                if len(payload)>8*1024*1024: raise ContractError("MRI plane JSON exceeds the upload profile")
                ref=context.artifacts.add_bytes(payload,"application/json",role)
                refs[ref["artifact_id"]]=ref
                frame[key]=None;frame[key+"_artifact_id"]=ref["artifact_id"]
    return bundled,list(refs.values())


def configure_parent(parent_root):
    root = Path(parent_root)
    if not (root / "backend/src/spine_backend/imaging.py").is_file():
        raise ContractError("Trusted parent imaging runtime is unavailable")
    sys.path.insert(0, str(root / "backend/src"))
    return root


def _base(source_ref, processing="replay"):
    return {"schema_version":"1.0","source_kind":"mri.source-grid.v1","mode":"recorded",
            "processing":processing,"acquired_at":None,
            "source":{"format":"mri","artifact_id":source_ref["artifact_id"],"sha256":source_ref["sha256"]},
            "registration_status":"unknown","model":None,"comparison_metadata":None,
            "provenance":{"adapter_version":"0.1.0","inference":"not_run_by_replay_bridge"},
            "frames":[],"volume_measurements":[],"warnings":[]}


def _measure(name,level,status="assessed",reason=None,line=None,method=None):
    return {"measurement_id":name+"-"+level,"concept":"spine.canal.cross_sectional_area" if name=="area" else "spine.canal.ap_extent",
            "level":level,"numbering_status":"needs_confirmation","assessment_status":status,
            "quality":"usable" if status=="assessed" else "unknown","method":method or "parent-source-grid-largest-component",
            "reason":reason,"line":line}


def _plane(image,array,normal_axis,index,frame_index,measurements):
    import numpy as np
    remaining_xyz=[axis for axis in (2,1,0) if axis!=normal_axis]
    row_axis,column_axis=remaining_xyz
    pixels=np.take(array,index,axis=2-normal_axis)
    spacing=image.GetSpacing(); direction=np.asarray(image.GetDirection()).reshape(3,3)
    origin_index=[0.,0.,0.]; origin_index[normal_axis]=float(index)
    origin=image.TransformContinuousIndexToPhysicalPoint(origin_index)
    return {"frame_index":frame_index,"frame_kind":"technical_source_grid_plane_not_necessarily_acquisition",
            "geometry":{"shape_rc":list(pixels.shape),"pixel_spacing_rc_mm":[float(spacing[row_axis]),float(spacing[column_axis])],
                        "origin_lps":list(origin),"row_direction_lps":direction[:,row_axis].tolist(),"column_direction_lps":direction[:,column_axis].tolist()},
            "image":pixels.astype(float).tolist(),"mask":None,"image_artifact_id":None,"mask_artifact_id":None,
            "mask_kind":"anatomy","structure":"spinal_canal","measurements":measurements}


def normalize_parent_export(payload, context, parent_root=None, processing="replay"):
    if parent_root is not None: configure_parent(parent_root)
    import numpy as np
    import SimpleITK as sitk
    from spine_backend.imaging import read_volume
    from spine_backend.levels import analyze_levels
    from spine_backend.segmentation import _validate_segmentation
    if type(payload)is not dict or not {"series","analysis"}<=set(payload):
        raise ContractError("Parent replay requires series and analysis linked export")
    series, analysis = payload["series"],payload["analysis"]
    source_path=Path(series["volume_path"]); mask_path=Path(analysis["segmentation"]["segmentation_path"])
    if source_path.is_symlink() or mask_path.is_symlink(): raise ContractError("MRI source and mask must be regular trusted files")
    image=read_volume(source_path); labels,_=_validate_segmentation(image,mask_path)
    source_ref=context.artifacts.add_file(source_path,"application/x-metaimage","source")
    mask_ref=context.artifacts.add_file(mask_path,"application/x-metaimage","derived")
    data=_base(source_ref,processing)
    data["acquired_at"]=series.get("acquired_at")
    data["comparison_metadata"]=payload.get("comparison_metadata")
    data["source"]["format"]=series.get("source_format","mha")
    array=sitk.GetArrayFromImage(image); label_array=sitk.GetArrayFromImage(labels)
    with tempfile.TemporaryDirectory() as temporary:
        measured=analyze_levels(source_path,mask_path,Path(temporary),series)
    geometry={"shape_xyz":list(image.GetSize()),"spacing_xyz_mm":list(image.GetSpacing()),
              "origin_lps":list(image.GetOrigin()),"direction":list(image.GetDirection())}
    data["provenance"].update(source_volume_sha256=source_ref["sha256"],segmentation_sha256=mask_ref["sha256"],
                               geometry_validated=True,recorded_model_provenance=analysis["segmentation"].get("provenance"),
                               sampling_policy=measured["sampling_policy"])
    # Cached claimed values are independently compared with the current masks.
    previous={level["level"]:level for level in analysis.get("levels",[])}
    for level in measured["levels"]:
        name=level["level"]; label=level["label"]
        points=np.argwhere(label_array==label)[:,::-1].astype(int).tolist()
        volume_measurement={"measurement_id":"disc-volume-"+name,"level":name,"numbering_status":"needs_confirmation",
            "geometry":geometry,"voxel_indices_xyz":points,"mask_artifact_id":mask_ref["artifact_id"],
            "method":"parent-full-source-grid-disc-label-voxel-count-times-voxel-volume"}
        data["volume_measurements"].append(volume_measurement)
        if level["status"]!="localized": continue
        disc_normal=series.get("acquisition_axis")
        if disc_normal is None: disc_normal=0
        disc_index=int(round(level["anatomy"]["representative_index_xyz"][disc_normal]))
        disc_frame=_plane(image,array,disc_normal,disc_index,len(data["frames"]),[])
        disc_frame.update(structure="intervertebral_disc",mask=(np.take(label_array,disc_index,axis=2-disc_normal)==label).astype(int).tolist())
        volume_measurement["display_frame_index"]=disc_frame["frame_index"]
        data["frames"].append(disc_frame)
        for measurement in level["measurements"]:
            cached=next((m for m in previous.get(name,{}).get("measurements",[]) if m.get("name")==measurement["name"]),None)
            if cached and cached.get("status")=="measured" and measurement.get("status")=="measured":
                if not np.isclose(float(cached["value"]),float(measurement["value"]),rtol=1e-5,atol=1e-5):
                    raise ContractError("Cached MRI measurement does not match its linked current mask")
            if measurement["name"]=="disc_mask_volume": continue
            evidence=measurement.get("evidence") or {}
            normal_axis=evidence.get("normal_grid_axis")
            normal_index=evidence.get("normal_index")
            if normal_axis is None:
                # Retain explicitly unassessed oblique results on a display-only native plane.
                normal_axis=series.get("acquisition_axis")
                if normal_axis is None: normal_axis=2
                normal_index=int(round(level["anatomy"]["centroid_index_xyz"][normal_axis]))
            normal_index=int(normal_index)
            status="assessed" if measurement.get("status")=="measured" else "not_assessed"
            kind="area" if measurement["name"]=="spinal_canal_transverse_area" else "ap"
            line=None
            if kind=="ap" and status=="assessed":
                # Normal LR leaves row SI, column AP on an axis-aligned source grid.
                inv=np.asarray([image.TransformPhysicalPointToContinuousIndex(tuple(point)) for point in evidence["line_endpoints_lps"]])
                remaining=[a for a in (2,1,0) if a!=normal_axis]
                if abs(np.asarray(image.GetDirection()).reshape(3,3)[1,remaining[1]])<.9999:
                    status="not_assessed"
                else:
                    first,last=sorted(inv[:,remaining[1]])
                    line={"row":int(round(float(inv[0,remaining[0]]))),"column_first":int(round(float(first+.5))),"column_last":int(round(float(last-.5)))}
            normalized=_measure(kind,name,status,measurement.get("reason"),line,
                                "parent-native-source-grid-largest-component" if kind=="area" else "parent-native-source-grid-patient-AP-line")
            frame=_plane(image,array,normal_axis,normal_index,len(data["frames"]),[normalized])
            frame["mask"]=(np.take(label_array,normal_index,axis=2-normal_axis)==2).astype(int).tolist()
            data["frames"].append(frame)
    if not data["frames"]:
        normal_axis=series.get("acquisition_axis") or 2
        index=image.GetSize()[normal_axis]//2
        frame=_plane(image,array,normal_axis,index,0,[_measure("area","L4-L5","not_assessed","lumbar_disc_labels_absent")])
        frame["mask"]=(np.take(label_array,index,axis=2-normal_axis)==2).astype(int).tolist(); data["frames"].append(frame)
    data["warnings"]=list(measured["warnings"])
    return data,[source_ref,mask_ref]


def normalize_spinosarc_export(payload,context,spinosarc_root,processing="replay"):
    root=Path(spinosarc_root)
    if not (root/"spinosarc_app/demo_io.py").is_file(): raise ContractError("Trusted SpinoSarc reader is unavailable")
    sys.path.insert(0,str(root))
    import numpy as np
    import nibabel as nib
    from spinosarc_app.demo_io import prepare_volumes
    from spinosarc_app.totalspineseg.canal_csa import resample_canal_to_axial_slice
    paths=[]
    for source in payload["sources"]:
        path=Path(source["path"])
        if path.is_symlink() or not path.exists(): raise ContractError("SpinoSarc linked source is unavailable")
        for entry in source.get("native_source_files",[]):
            current=Path(entry["path"])
            if current.is_symlink() or not current.is_file() or current.stat().st_size!=entry["size_bytes"] or current.stat().st_mtime_ns!=entry["mtime_ns"]:
                raise ContractError("SpinoSarc source identity changed since its export")
        paths.append(path)
    canal=Path(payload["canal_nifti_path"])
    if canal.is_symlink() or not canal.is_file(): raise ContractError("SpinoSarc linked canal mask is unavailable")
    canal_image=nib.load(str(canal))
    if len(canal_image.shape)!=3 or int(np.prod(canal_image.shape))>100000000 or canal_image.header.get_xyzt_units()[0]!="mm":
        raise ContractError("SpinoSarc canal mask requires bounded 3D millimetre geometry")
    affine=np.asarray(canal_image.affine,dtype=float);spacing=np.linalg.norm(affine[:3,:3],axis=0)
    if not np.isfinite(affine).all() or not (spacing>0).all() or not np.allclose((affine[:3,:3]/spacing).T@(affine[:3,:3]/spacing),np.eye(3),atol=1e-3):
        raise ContractError("SpinoSarc canal mask geometry is invalid")
    if not np.isfinite(np.asanyarray(canal_image.dataobj)).all(): raise ContractError("SpinoSarc canal mask has invalid pixels")
    references=[]
    with tempfile.TemporaryDirectory() as temporary:
        prepared=prepare_volumes(paths,Path(temporary)/"prepared")
        # Native files remain exact source artifacts; no path or DICOM UID enters the public bundle.
        for path in paths:
            files=sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
            for file in files: references.append(context.artifacts.add_file(file,"application/dicom" if path.is_dir() else "application/octet-stream","source"))
        canal_ref=context.artifacts.add_file(canal,"application/nifti","derived"); references.append(canal_ref)
        data=_base(references[0],processing); data["source"]["format"]="spinosarc-native"
        data["acquired_at"]=payload.get("acquired_at")
        data["comparison_metadata"]=payload.get("comparison_metadata")
        data["registration_status"]="needs_review" if prepared["registration_status"]!="shared_dicom_frame_of_reference" else "confirmed"
        data["provenance"].update(adapter="spinosarc-step1-native-plane",canal_mask_sha256=canal_ref["sha256"],
            source_artifact_ids=[ref["artifact_id"] for ref in references if ref["role"]=="source"],
            canal_interpolation="linear_soft_mask_threshold_0.5",recorded_inference_returncode=payload.get("segmentation",{}).get("actual_process_returncode"),
            numbering="needs_confirmation",muscles="not_imported_in_first_profile",unassessed_levels=[])
        levels=payload.get("multi_level_measurements",{}).get("levels",{})
        for name,saved in levels.items():
            level="L5-S1" if name=="L5-S" else name
            if level not in {"L1-L2","L2-L3","L3-L4","L4-L5","L5-S1"}: continue
            index=saved.get("axial_slice_idx")
            covered=saved.get("covered") is True and not saved.get("approx",False)
            if index is None or not covered:
                data["provenance"]["unassessed_levels"].append({"level":level,"reason":"native_axial_level_outside_coverage_or_approximate"})
                continue
            if type(index)is not int or not 0<=index<len(prepared["axial_slices"]): raise ContractError("SpinoSarc source frame index is invalid")
            native=prepared["axial_slices"][index]
            mask=resample_canal_to_axial_slice(str(canal),native,threshold=.5)
            if mask is None: raise ContractError("SpinoSarc canal mask cannot be placed on its native source plane")
            calculated=float(mask.sum()*np.prod(native["pixel_spacing"]))
            if saved.get("canal_csa_mm2") is not None and not np.isclose(calculated,float(saved["canal_csa_mm2"]),rtol=1e-5,atol=1e-5):
                raise ContractError("SpinoSarc CSA does not match its current linked source and mask")
            measurement=_measure("area",level,method="spinosarc-native-plane-all-mask-pixels")
            data["frames"].append({"frame_index":index,"frame_kind":"native_axial_acquisition_plane",
                "geometry":{"shape_rc":list(mask.shape),"pixel_spacing_rc_mm":list(native["pixel_spacing"]),
                    "origin_lps":list(native["image_position"]),"row_direction_lps":list(native["image_orientation"][3:]),
                    "column_direction_lps":list(native["image_orientation"][:3])},
                "image":native["pixel_array"].astype(float).tolist(),"mask":mask.astype(int).tolist(),
                "image_artifact_id":None,"mask_artifact_id":None,"mask_kind":"anatomy","structure":"spinal_canal","measurements":[measurement]})
        if not data["frames"]: raise ContractError("SpinoSarc export has no covered native axial measurement")
        data["warnings"]=list(prepared["geometry_warnings"])+["SpinoSarc threshold flags, dural-sac wording and sarcopenia diagnosis are not imported."]
    return data,references


def import_export(export_path,context,request,parent_root=None,spinosarc_root=None):
    path=Path(export_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8*1024*1024: raise ContractError("MRI export must be a bounded regular trusted JSON file")
    payload=loads_json(path.read_bytes())
    if type(payload)is dict and payload.get("source_kind")=="mri.source-grid.v1":
        data,references=payload,[]
    elif type(payload)is dict and {"series","analysis"}<=set(payload):
        data,references=normalize_parent_export(payload,context,parent_root)
    elif type(payload)is dict and {"sources","canal_nifti_path","multi_level_measurements"}<=set(payload):
        if spinosarc_root is None: raise ContractError("SpinoSarc replay requires its trusted reader root")
        data,references=normalize_spinosarc_export(payload,context,spinosarc_root)
    else: raise ContractError("Unsupported MRI export profile or missing linked source frames")
    normalized_ref=context.artifacts.add_bytes(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode(),"application/json","derived")
    return analyze_source(data,request,context,normalized_ref,references)
