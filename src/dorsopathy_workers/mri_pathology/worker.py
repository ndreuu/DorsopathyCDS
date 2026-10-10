"""Fresh isolated SpineNet research inference, with exact model-input evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from dorsopathy_platform.contracts import ContractError, loads_json, validate_result
from dorsopathy_platform.process_io import read_worker_request, write_worker_output
from dorsopathy_platform.results import result_envelope
from dorsopathy_workers.mri.worker import WorkerArtifacts
from .geometry import read_sagittal_volume

UPSTREAM_COMMIT = "5e96ac54b1c1845a37d12e40bfd16f647d40acd8"
WEIGHT_FILES = ("grading/ckpt1.pt", "appearance/ckpt187.pt", "detect-vfr/ckpt435.pt", "context/ckpt16.pt")
LEVELS = ("L1-L2", "L2-L3", "L3-L4", "L4-L5", "L5-S1")


def verify_assets(root):
    root = Path(root).resolve()
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=15).strip()
    if commit != UPSTREAM_COMMIT:
        raise ContractError("SpineNet code is not the pinned revision")
    if subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", "spinenet"], check=False, timeout=15).returncode:
        raise ContractError("SpineNet source has tracked modifications")
    weight_root = root / "spinenet/weights"
    manifest_path = weight_root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ContractError("SpineNet asset manifest must be a regular file")
    with manifest_path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ContractError("SpineNet asset manifest exceeds limit")
    manifest = loads_json(content)
    license_url = "https://github.com/rwindsor1/SpineNet/blob/" + commit + "/LICENCE.md"
    if (type(manifest) is not dict or manifest.get("schema_version") != "spinenet-assets.v1"
        or manifest.get("upstream_commit") != commit or manifest.get("license_url") != license_url
        or type(manifest.get("files")) is not dict or set(manifest["files"]) != set(WEIGHT_FILES)):
        raise ContractError("SpineNet asset manifest is invalid")
    actual = {}
    for relative in WEIGHT_FILES:
        path = weight_root / relative
        entry = manifest["files"][relative]
        expected_url = "https://www.robots.ox.ac.uk/~vgg/research/spinenet/weights/" + relative
        if (type(entry) is not dict or set(entry) != {"url", "sha256", "size_bytes"}
            or entry["url"] != expected_url or type(entry["size_bytes"]) is not int
            or not 0 < entry["size_bytes"] <= 1024**3 or type(entry["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
            raise ContractError("SpineNet checkpoint manifest entry is invalid")
        if path.is_symlink() or not path.is_file() or list(path.parent.glob("*.pt")) != [path]:
            raise ContractError("SpineNet checkpoint selection must be unambiguous")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"] or path.stat().st_size != entry["size_bytes"]:
            raise ContractError("SpineNet checkpoint bytes do not match their manifest")
        actual[relative] = {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size, "url": entry["url"]}
    return actual


def run_inference(source_path, run_dir, args, request, artifacts):
    import numpy as np
    import torch
    from PIL import Image, ImageDraw
    from dorsopathy_modules.mri_pathology import build_pathology_result

    weights = verify_assets(args.upstream_root)
    if int(np.__version__.split(".")[0]) >= 2:
        raise ContractError("Pinned SpineNet preprocessing requires NumPy below version 2")
    sys.path.insert(0, str(args.upstream_root.resolve()))
    import spinenet
    if Path(spinenet.__file__).resolve().parent != args.upstream_root.resolve() / "spinenet":
        raise ContractError("SpineNet import did not use the pinned source directory")
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    volume, spacing, image, geometry = read_sagittal_volume(source_path, run_dir, args.contrast)
    started = time.monotonic()
    network = spinenet.SpineNet(device=args.device, scan_type="lumbar", verbose=True)
    verts = network.detect_vb(volume, spacing)
    if len(verts) < 2:
        raise ContractError("SpineNet did not find enough vertebrae to extract disc regions")
    # Non-debug detect_vb deliberately removes internal vertebral crops.
    # Recreate them with the exact upstream routine solely for QC, without
    # modifying/replacing the detection output consumed by native IVD extraction.
    from spinenet.utils.extract_volumes import extract_volumes
    qc_verts = extract_volumes(volume, [dict(vert) for vert in verts])
    for vert in qc_verts:
        body = np.asarray(vert.get("volume"))
        if not np.isfinite(body).all() or np.ptp(body) <= 0:
            raise ContractError("SpineNet produced an invalid vertebral crop")
    # Pinned upstream poly2mask still refers to the removed np.bool alias.
    # Restore its historical boolean dtype only for this isolated crop call,
    # then restore NumPy exactly. No upstream file or crop algorithm changes.
    had_bool_alias = "bool" in np.__dict__
    if not had_bool_alias:
        np.bool = np.bool_
    try:
        native_ivds = network.get_ivds_from_vert_dicts(verts, volume)
    finally:
        if not had_bool_alias:
            del np.bool
    label_map = {}
    for vert in verts:
        label = vert["predicted_label"]
        if label in label_map:
            raise ContractError("SpineNet vertebral numbering is ambiguous")
        label_map[label] = vert
    levels, skipped = [], []
    for ivd in native_ivds:
        level = ivd["level_name"]
        if level not in LEVELS:
            continue
        crop = np.asarray(ivd["volume"], dtype=np.float32)
        if crop.shape != (9, 112, 224) or not np.isfinite(crop).all() or np.ptp(crop) <= 0:
            skipped.append({"level": level, "reason": "nonfinite_or_constant_model_crop"})
            continue
        upper, lower = level.split("-")
        if upper not in label_map or lower not in label_map:
            skipped.append({"level": level, "reason": "adjacent_vertebra_not_detected"})
            continue
        context = [label_map[upper], label_map[lower]]
        frame = int(np.round(np.mean([np.round(np.median(v["slice_nos"])) for v in context])))
        if frame - 4 < 0 or frame + 4 >= volume.shape[2]:
            skipped.append({"level": level, "reason": "model_input_requires_padded_slices"})
            continue
        if any(np.ptp(crop[slice_index]) <= 0 for slice_index in range(9)):
            skipped.append({"level": level, "reason": "model_input_contains_constant_slice"})
            continue
        with torch.no_grad():
            heads = network.grading_model(torch.from_numpy(crop)[None, None].to(args.device))
        scores = {}
        for name, index, count in (("CentralCanalStenosis", 2, 4), ("Herniation", 10, 2)):
            head = heads[index].reshape(-1)
            if head.numel() != count or not torch.isfinite(head).all():
                raise ContractError("SpineNet classifier output is invalid")
            scores[name] = torch.softmax(head, dim=0).cpu().tolist()
        grades = {name: int(np.argmax(values)) + (1 if name == "CentralCanalStenosis" else 0) for name, values in scores.items()}
        array_path = run_dir / (level + "-model-input.npy")
        np.save(array_path, crop, allow_pickle=False)
        model_ref = artifacts.add_file(array_path, "application/x-npy", "derived")
        montage = Image.new("L", (224 * 3, 112 * 3))
        for slice_index in range(9):
            display = np.rint(np.clip(crop[slice_index], 0, 1) * 255).astype(np.uint8)
            montage.paste(Image.fromarray(display), ((slice_index % 3) * 224, (slice_index // 3) * 112))
        montage_path = run_dir / (level + "-model-input.png")
        montage.save(montage_path)
        ivd_ref = artifacts.add_file(montage_path, "image/png", "derived")
        source_plane = volume[:, :, frame]
        lo, hi = np.percentile(source_plane, [.5, 99.5])
        source_display = np.rint(np.clip((source_plane - lo) / max(float(hi - lo), 1e-6), 0, 1) * 255).astype(np.uint8)
        source_image = Image.fromarray(source_display).convert("RGB")
        draw = ImageDraw.Draw(source_image)
        context_polygons = []
        for v in context:
            if frame in v["slice_nos"]:
                poly = np.asarray(v["polys"][list(v["slice_nos"]).index(frame)])
                polygon_method = "detected_on_displayed_slice"
            else:
                poly = np.asarray(v["average_polygon"])
                polygon_method = "average_vertebral_polygon"
            if poly.shape != (4, 2) or not np.isfinite(poly).all():
                raise ContractError("SpineNet vertebral context polygon is invalid")
            points = [tuple(map(float, point)) for point in poly]
            draw.line(points + [points[0]], fill=(72, 205, 220), width=2)
            draw.text(points[0], v["predicted_label"], fill=(72, 205, 220))
            context_polygons.append({"label": v["predicted_label"], "points_xy": poly.tolist(), "method": polygon_method})
        draw.text((8, 8), level + " | vertebral context, not pathology contour", fill=(255, 225, 100))
        source_png = run_dir / (level + "-source.png")
        source_image.save(source_png)
        source_ref = artifacts.add_file(source_png, "image/png", "derived")
        levels.append({"level": level, "grades": grades, "scores": scores,
                       "evidence": {"source_frame_index": frame, "source_png_artifact_id": source_ref["artifact_id"],
                                    "ivd_png_artifact_id": ivd_ref["artifact_id"], "model_input_artifact_id": model_ref["artifact_id"]}})
        geometry.setdefault("level_evidence", {})[level] = {"vertebral_context": context_polygons,
            "model_input_source_frames": list(range(frame - 4, frame + 5)),
            "model_input_preprocessing": "unmodified_SpineNet_get_ivds_from_vert_dicts",
            "crop_transform_to_source": "not_exported_by_upstream; no_pathology_contour_or_mm_size"}
    levels.sort(key=lambda item: LEVELS.index(item["level"]))
    if not levels:
        raise ContractError("SpineNet produced no supported usable lumbar disc crops")
    raw = {"schema_version": "mri.spinenet.v1", "processing": "fresh",
           "source": {"artifact_id": request["input_artifacts"][0]["artifact_id"], "sha256": request["input_artifacts"][0]["sha256"]},
           "model": {"id": "spinenet-v2-grading", "version": UPSTREAM_COMMIT[:8], "weights_sha256": weights["grading/ckpt1.pt"]["sha256"]},
           "levels": levels, "acquired_at": None,
           "provenance": {"acquisition": args.acquisition, "upstream_commit": UPSTREAM_COMMIT,
               "weights": weights, "preprocessing": geometry, "duration_seconds": time.monotonic() - started,
               "runtime": {"torch": str(torch.__version__), "numpy": str(np.__version__), "device": args.device},
               "skipped_levels": skipped, "heads": [2, 10], "scores_are_uncalibrated": True,
               "clinical_rule_inputs_populated": False, "pathology_segmentation": False,
               "source_code_modified": False, "existing_predictions_reused": False,
               "compatibility_shims": [] if had_bool_alias else ["temporary_numpy_bool_dtype_alias_for_upstream_poly2mask"],
               "license_url": "https://github.com/rwindsor1/SpineNet/blob/" + UPSTREAM_COMMIT + "/LICENCE.md"}}
    raw_path = run_dir / "pathology.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    sidecar = artifacts.add_file(raw_path, "application/json", "derived")
    result = build_pathology_result(raw, request, sidecar, list(artifacts.refs.values()))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange-dir", type=Path, required=True)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu",), default="cpu")
    parser.add_argument("--contrast", choices=("T2",), required=True)
    parser.add_argument("--acquisition", choices=("recorded", "synthetic"), default="recorded")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.threads <= 8:
        parser.error("threads must be between 1 and 8")
    envelope = read_worker_request(args.exchange_dir)
    request = envelope["request"]
    artifacts = WorkerArtifacts(args.exchange_dir, envelope)
    run_dir = args.exchange_dir / "fresh-pathology"
    run_dir.mkdir()
    try:
        if (request["module_id"], request["module_version"]) != ("mri.pathology", "0.1.0") or request["parameters"] != {"processing_mode": "live"} or len(request["input_artifacts"]) != 1:
            raise ContractError("Pathology worker requires one raw MRI and processing_mode live")
        source_path = artifacts.paths[request["input_artifacts"][0]["artifact_id"]]
        result = run_inference(source_path, run_dir, args, request, artifacts)
    except Exception as error:
        traceback.print_exc()
        audit = artifacts.add_bytes(json.dumps({"schema_version": "1.0", "processing": "fresh", "status": "failed",
            "error_type": type(error).__name__, "reason": str(error) if isinstance(error, ContractError) else None,
            "fresh_inference_successful": False}).encode(), "application/json", "derived")
        result = result_envelope(request, [], [*request["input_artifacts"], audit], "failed")
        result["error"] = {"code": "mri_pathology_failed", "message": "Fresh MRI pathology extraction failed; no diagnostic finding was inferred."}
    result = validate_result(result, request)
    write_worker_output(args.exchange_dir, result, artifacts.descriptors(result))
    return 0
