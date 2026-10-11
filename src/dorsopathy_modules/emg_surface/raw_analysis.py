"""Frozen emg-raw-csv.v2: explicit metadata, contiguous-run processing, no diagnosis."""
import csv
from datetime import datetime, timedelta
import gzip
import io
import math
from pathlib import Path
import tempfile

from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.results import result_envelope
from ._support import COMMON_FIELDS, add_json, calibration, common, metadata, number, observation, read_source, require, text
from .raw_provider import RAW_EMG_SPEC

MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_SAMPLES = 1000000
MAX_WINDOWS = 8192
MAX_WINDOW_VALUES = 8 * 1024 * 1024
HIPBRIDGE_COLUMNS = [
    "Frame", "Time", "Left Rectus femoris", "Right Rectus femoris",
    "Right Gluteus maximus", "Left Gluteus maximus",
    "Left Erector spinae longissimus", "Right Erector spinae longissimus",
    "Left Biceps femoris caput longus", "Right Biceps femoris caput longus",
]


def validate_manifest(data):
    require(data, COMMON_FIELDS | {
        "csv_artifact_id", "export", "sample_rate_hz", "channels", "device",
        "events", "processing",
    })
    common(data, "emg-raw-csv.v2", 1)
    calibration(data)
    require(data["device"], {"manufacturer", "model", "serial", "upstream_filtering"})
    for value in data["device"].values():
        if value is not None:
            text(value)
    export = require(data["export"], {"format", "time_column", "ignored_columns"})
    if export["format"] not in {"csv", "csv_gzip", "hipbridgeemg.v0.1"}:
        raise ContractError("Unsupported raw EMG export format")
    text(export["time_column"])
    if type(export["ignored_columns"]) is not list:
        raise ContractError("Ignored export columns must be explicit")
    for column in export["ignored_columns"]:
        text(column)
    rate = None if data["sample_rate_hz"] is None else number(data["sample_rate_hz"], positive=True)
    if rate is not None and rate > 100000:
        raise ContractError("EMG rate exceeds supported profile")
    channels = data["channels"]
    if type(channels) is not list or not 1 <= len(channels) <= 8:
        raise ContractError("Raw EMG requires one to eight channels")
    ids, columns = set(), [export["time_column"], *export["ignored_columns"]]
    for channel in channels:
        require(channel, {"id", "column", "side", "muscle", "electrode_placement", "calibration", "adc_rails"})
        opaque_id(channel["id"])
        text(channel["column"])
        text(channel["muscle"])
        if channel["id"] in ids or channel["side"] not in {"left", "right", "midline", "bilateral", "unspecified"}:
            raise ContractError("Invalid EMG channel identity or side")
        ids.add(channel["id"])
        columns.append(channel["column"])
        if channel["electrode_placement"] is not None:
            text(channel["electrode_placement"])
        cal = calibration(channel, {"uV_per_unit"})
        factor = cal["uV_per_unit"]
        if factor is not None:
            number(factor, positive=True)
        if cal["status"] == "verified" and factor is None:
            raise ContractError("Verified channel calibration requires microvolt scale")
        rails = channel["adc_rails"]
        if rails is not None:
            require(rails, {"lower", "upper"})
            if number(rails["lower"]) >= number(rails["upper"]):
                raise ContractError("ADC rails must be an increasing source-unit interval")
    if len(columns) != len(set(columns)):
        raise ContractError("Raw EMG columns must be unique")
    if export["format"] == "hipbridgeemg.v0.1":
        if set(columns) != set(HIPBRIDGE_COLUMNS) or export["time_column"] != "Time" or rate != 1000:
            raise ContractError("HipBridge export requires its published columns and 1000 Hz")
    processing = require(data["processing"], {
        "detrend", "bandpass_hz", "butterworth_order", "notch", "filter_pad_samples",
        "edge_guard_samples", "envelope_samples", "window_samples", "hop_samples",
        "welch_segment_samples", "welch_overlap_samples", "timing_tolerance_fraction",
    })
    if processing["detrend"] not in {"none", "constant"}:
        raise ContractError("Unsupported detrend")
    for key, low, high in (
        ("butterworth_order", 1, 8), ("filter_pad_samples", 0, 4096),
        ("edge_guard_samples", 0, 8192), ("envelope_samples", 1, 4095),
        ("window_samples", 16, 16384), ("hop_samples", 1, 16384),
        ("welch_segment_samples", 16, 4096), ("welch_overlap_samples", 0, 4095),
    ):
        value = processing[key]
        if type(value) is not int or not low <= value <= high:
            raise ContractError("Processing integer exceeds supported profile: " + key)
    if processing["envelope_samples"] % 2 != 1:
        raise ContractError("Centered moving envelope requires an odd sample count")
    if processing["hop_samples"] > processing["window_samples"]:
        raise ContractError("EMG hop must not exceed window")
    if processing["welch_segment_samples"] > processing["window_samples"] or processing["welch_overlap_samples"] >= processing["welch_segment_samples"]:
        raise ContractError("Invalid explicit Welch window or overlap")
    tolerance = number(processing["timing_tolerance_fraction"])
    if not 0 <= tolerance < 0.5:
        raise ContractError("Invalid timestamp tolerance")
    band = processing["bandpass_hz"]
    if band is not None:
        if type(band) is not list or len(band) != 2 or rate is None:
            raise ContractError("Bandpass requires explicit known sampling rate")
        low, high = [number(value, positive=True) for value in band]
        if not low < high < rate / 2:
            raise ContractError("Bandpass must lie strictly below Nyquist")
    notch = processing["notch"]
    if notch is not None:
        require(notch, {"frequency_hz", "q"})
        if rate is None or not 0 < number(notch["frequency_hz"], positive=True) < rate / 2:
            raise ContractError("Notch frequency must lie below known Nyquist")
        number(notch["q"], positive=True)
    filtering = band is not None or notch is not None
    if filtering and (processing["filter_pad_samples"] == 0 or processing["edge_guard_samples"] < processing["filter_pad_samples"]):
        raise ContractError("Filtering requires explicit padding and at least the same edge guard")
    if not filtering and (processing["filter_pad_samples"] != 0 or processing["edge_guard_samples"] != 0):
        raise ContractError("Unfiltered processing must declare zero filter padding and guard")
    events = data["events"]
    if type(events) is not list or len(events) > 128:
        raise ContractError("Invalid recorded event list")
    event_ids = set()
    previous_stop = None
    for event in events:
        require(event, {"id", "label", "start_s", "stop_s", "source"})
        opaque_id(event["id"])
        text(event["label"])
        text(event["source"])
        start, stop = number(event["start_s"]), number(event["stop_s"])
        if event["id"] in event_ids or start < 0 or stop <= start or previous_stop is not None and start < previous_stop:
            raise ContractError("Recorded task intervals must be ordered, unique and nonoverlapping")
        event_ids.add(event["id"])
        previous_stop = stop
    return rate


def read_csv(reference, context, data):
    if reference["size_bytes"] > MAX_CSV_BYTES:
        raise ContractError("Raw EMG export exceeds size limit")
    export = data["export"]
    rows, previous, decoded_bytes = [], None, 0
    with context.artifacts.open(reference) as binary:
        stream = gzip.GzipFile(fileobj=binary) if export["format"] != "csv" else binary
        try:
            reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
            header = next(reader, None)
            expected = [export["time_column"], *export["ignored_columns"], *[channel["column"] for channel in data["channels"]]]
            if not header or len(header) != len(set(header)) or set(header) != set(expected):
                raise ContractError("Raw EMG CSV headers differ from the manifest")
            if export["format"] == "hipbridgeemg.v0.1" and header != HIPBRIDGE_COLUMNS:
                raise ContractError("HipBridge raw export headers differ from the published format")
            ti = header.index(export["time_column"])
            ci = [header.index(channel["column"]) for channel in data["channels"]]
            for raw in reader:
                decoded_bytes += sum(len(value.encode("utf-8")) + 1 for value in raw)
                if len(raw) != len(header) or decoded_bytes > MAX_CSV_BYTES:
                    raise ContractError("Raw EMG rows or expanded size exceed the profile")
                def parse(value):
                    if value.strip() in {"", "NA", "NaN", "nan"}:
                        return None, "missing"
                    parsed = float(value)
                    return (parsed, None) if math.isfinite(parsed) else (None, "nonfinite")
                stamp, stamp_reason = parse(raw[ti])
                if stamp is not None:
                    if stamp < 0 or previous is not None and stamp <= previous:
                        raise ContractError("Raw EMG timestamps must be nonnegative and increasing")
                    previous = stamp
                values, reasons = zip(*(parse(raw[index]) for index in ci))
                rows.append((stamp, list(values), stamp_reason, list(reasons)))
                if len(rows) > MAX_SAMPLES:
                    raise ContractError("Raw EMG sample count exceeds limit")
        except (UnicodeDecodeError, ValueError, csv.Error, OSError, EOFError):
            raise ContractError("Raw EMG CSV or gzip is invalid") from None
    if not rows:
        raise ContractError("Raw EMG export is empty")
    return rows


def _stamp(acquired, seconds):
    if acquired is None or seconds is None:
        return None
    value = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
    return (value + timedelta(seconds=seconds)).isoformat()


def analyze_raw(request, context):
    # Heavy numeric libraries are optional and imported inside the worker only.
    import numpy as np
    import scipy
    from scipy import signal
    request, data, source = read_source(request, context, RAW_EMG_SPEC)
    rate = validate_manifest(data)
    references = {ref["artifact_id"]: ref for ref in request["input_artifacts"]}
    raw_ref = references.get(data["csv_artifact_id"])
    if len(references) != 2 or raw_ref is None or raw_ref["role"] != "source" or raw_ref["media_type"] not in {"text/csv", "application/csv", "application/gzip"}:
        raise ContractError("Raw EMG requires exactly its manifest and raw export")
    if request["protocol_id"] != data["protocol_id"]:
        raise ContractError("Request and raw manifest protocols differ")
    rows = read_csv(raw_ref, context, data)
    count, channels, p = len(rows), data["channels"], data["processing"]
    times = [row[0] for row in rows]
    window, hop = p["window_samples"], p["hop_samples"]
    if math.ceil(count / hop) * len(channels) > MAX_WINDOWS or math.ceil(count / hop) * len(channels) * window > MAX_WINDOW_VALUES:
        raise ContractError("Raw EMG window expansion exceeds bounded profile")
    if data["events"]:
        if times[0] is None or times[-1] is None or rate is None or any(event["start_s"] < times[0] or event["stop_s"] > times[-1] + 1 / rate + 1e-9 for event in data["events"]):
            raise ContractError("Recorded events must lie inside the known sample interval")
    timing_bad = [None] * count
    for index, stamp in enumerate(times):
        if stamp is None:
            timing_bad[index] = "nonfinite_timestamp" if rows[index][2] == "nonfinite" else "missing_timestamp"
        elif index and times[index - 1] is not None and rate is not None and abs((stamp - times[index - 1]) - 1 / rate) > p["timing_tolerance_fraction"] / rate:
            timing_bad[index] = "sample_gap_or_rate_conflict"
    filters = []
    if p["bandpass_hz"] is not None:
        filters.append(signal.butter(p["butterworth_order"], p["bandpass_hz"], btype="bandpass", fs=rate, output="sos"))
    if p["notch"] is not None:
        notch = p["notch"]
        b, a = signal.iirnotch(notch["frequency_hz"], notch["q"], fs=rate)
        filters.append(signal.tf2sos(b, a))
    method = {
        "schema_version": "emg-signal-method.v2", "implementation": "scipy-contiguous-runs.v1",
        "numpy_version": np.__version__, "scipy_version": scipy.__version__,
        "processing": p, "sos": [value.tolist() for value in filters],
        "filter_application": "sosfiltfilt sequential bandpass then notch; odd padding; explicit padlen; no resampling",
        "rms": "sqrt(mean(filtered_x**2))",
        "envelope": "centered moving mean(abs(filtered_x)); window mean of envelope; no padding",
        "median_frequency": "first Welch PSD bin reaching 50 percent of summed equal-width bin powers; no interpolation",
        "welch": {"window": "periodic_hann", "detrend": False, "nfft": p["welch_segment_samples"], "scaling": "density", "average": "mean", "return_onesided": True},
        "clinical_interpretation": "descriptive signal features; no normal/fatigue/radiculopathy threshold",
    }
    guard = max(p["edge_guard_samples"], p["envelope_samples"] // 2)
    processed, envelopes, bads, channel_qc = [], [], [], []
    for channel_index, channel in enumerate(channels):
        values = [row[1][channel_index] for row in rows]
        bad = [timing_bad[index] for index in range(count)]
        counts = {"missing_signal_samples": 0, "nonfinite_signal_samples": 0, "clipped_samples": 0}
        rails = channel["adc_rails"]
        for index, value in enumerate(values):
            if value is None:
                reason = rows[index][3][channel_index]
                key = "nonfinite_signal_samples" if reason == "nonfinite" else "missing_signal_samples"
                counts[key] += 1
                bad[index] = key[:-1]
            elif rails is not None and (value <= rails["lower"] or value >= rails["upper"]):
                counts["clipped_samples"] += 1
                bad[index] = "clipping"
        output = np.full(count, np.nan)
        envelope = np.full(count, np.nan)
        runs = []
        start = 0
        while start < count:
            if bad[start] is not None:
                start += 1
                continue
            stop = start + 1
            while stop < count and bad[stop] is None:
                stop += 1
            runs.append({"sample_start": start, "sample_stop_exclusive": stop})
            if stop - start <= max(p["filter_pad_samples"], 2 * guard):
                for index in range(start, stop):
                    bad[index] = "contiguous_segment_too_short"
            else:
                segment = np.asarray(values[start:stop], dtype=float)
                if p["detrend"] == "constant":
                    segment = segment - np.mean(segment)
                cal = channel["calibration"]
                if cal["status"] == "verified":
                    segment = segment * cal["uV_per_unit"]
                for sos in filters:
                    segment = signal.sosfiltfilt(sos, segment, padtype="odd", padlen=p["filter_pad_samples"])
                if not np.isfinite(segment).all():
                    raise ContractError("Numeric processing produced a nonfinite signal")
                output[start:stop] = segment
                width = p["envelope_samples"]
                half = width // 2
                smoothed = np.convolve(np.abs(segment), np.ones(width) / width, mode="valid")
                envelope[start + half:stop - half if half else stop] = smoothed
                for index in list(range(start, start + guard)) + list(range(stop - guard, stop)):
                    bad[index] = "processing_edge_guard"
            start = stop
        processed.append(output)
        envelopes.append(envelope)
        bads.append(bad)
        channel_qc.append({
            "channel_id": channel["id"], **{**counts, "clipped_samples": counts["clipped_samples"] if rails is not None else None}, "contiguous_runs": runs,
            "amplitude_scale_known": channel["calibration"]["status"] == "verified",
            "clipping_verifiable": rails is not None,
            "unknowns": (["clipping_unverifiable"] if rails is None else []) + (["amplitude_scale_unknown"] if channel["calibration"]["status"] != "verified" else []) + (["sampling_rate_unknown"] if rate is None else []),
        })
    measurements = []
    ranges = data["events"] or [{"id": None, "label": "whole_recording", "source": None, "start_s": None, "stop_s": None}]
    for interval in ranges:
        eligible = [index for index, stamp in enumerate(times) if interval["id"] is None or stamp is not None and interval["start_s"] <= stamp < interval["stop_s"]]
        if not eligible:
            continue
        first, stop = eligible[0], eligible[-1] + 1
        starts = list(range(first, max(first + 1, stop - window + 1), hop))
        if starts[-1] + window < stop:
            starts.append(starts[-1] + hop)
        if len(measurements) + len(starts) * len(channels) > MAX_WINDOWS or (len(measurements) + len(starts) * len(channels)) * window > MAX_WINDOW_VALUES:
            raise ContractError("Task interval window schedule exceeds bounded profile")
        for start in starts:
            end = min(start + window, stop)
            for channel_index, channel in enumerate(channels):
                reasons = sorted(set(reason for reason in bads[channel_index][start:end] if reason is not None))
                if end - start != window:
                    reasons.append("incomplete_window")
                cal = channel["calibration"]
                known = cal["status"] == "verified"
                quality = "unreliable" if reasons else "unknown" if channel["adc_rails"] is None or rate is None or cal["status"] == "invalid" else "usable"
                rms, env, mdf, psd, frequencies = None, None, None, [], []
                if not reasons and cal["status"] != "invalid":
                    segment = processed[channel_index][start:end]
                    if known:
                        rms = float(np.sqrt(np.mean(segment ** 2)))
                        env = float(np.mean(envelopes[channel_index][start:end]))
                    if rate is not None:
                        frequencies_array, power = signal.welch(
                            segment, fs=rate, window=signal.get_window("hann", p["welch_segment_samples"], fftbins=True),
                            nperseg=p["welch_segment_samples"], noverlap=p["welch_overlap_samples"],
                            nfft=p["welch_segment_samples"], detrend=False, return_onesided=True, scaling="density", average="mean",
                        )
                        frequencies, psd = frequencies_array.tolist(), power.tolist()
                        total = float(np.sum(power))
                        if total > 0:
                            mdf = float(frequencies_array[np.searchsorted(np.cumsum(power), total / 2)])
                        else:
                            quality = "unreliable"
                            reasons.append("zero_spectral_power")
                measurements.append({
                    "id": channel["id"] + "-" + str(start) + "-" + (interval["id"] or "record"),
                    "channel": channel, "event": interval if interval["id"] is not None else None,
                    "sample_start": start, "sample_stop_exclusive": end,
                    "start_s": times[start], "stop_s": times[end - 1] + 1 / rate if times[end - 1] is not None and rate is not None else None,
                    "rms_uV": rms, "envelope_mean_uV": env, "median_frequency_Hz": mdf,
                    "quality": quality, "qc_reasons": reasons,
                    "spectrum_frequency_Hz": frequencies, "spectrum_psd": psd,
                    "spectrum_psd_unit": "uV2/Hz" if known else "source_units_squared/Hz",
                })
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "processed.csv"
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sample_index", "time_s", "channel_id", "processed_signal", "envelope", "unit", "qc_reason"])
            for index in range(count):
                for ci, channel in enumerate(channels):
                    value, env = processed[ci][index], envelopes[ci][index]
                    writer.writerow([index, times[index], channel["id"], float(value) if np.isfinite(value) else "", float(env) if np.isfinite(env) else "", "uV" if channel["calibration"]["status"] == "verified" else "source_units", bads[ci][index] or ""])
        processed_ref = context.artifacts.add_file(path, "text/csv", role="derived")
    from .raw_plot import plot_signals
    previews = plot_signals(context, rows, processed, envelopes, channels, data["events"])
    method_ref = add_json(context, method)
    statuses = [channel["calibration"]["status"] for channel in channels]
    comparison_status = "invalid" if "invalid" in statuses or data["calibration"]["status"] == "invalid" else "verified" if all(value == "verified" for value in statuses) and data["calibration"]["status"] == "verified" else "unknown"
    comparison = metadata(data, source, None, comparison_status)
    comparison["acquired_interval"] = {"start": _stamp(data["acquired_at"], times[0]), "end": _stamp(data["acquired_at"], times[-1] + 1 / rate if times[-1] is not None and rate is not None else None), "clock_domain": data["clock_domain"]}
    summary = {
        "schema_version": "emg-raw-measurements.v2", "source_kind": "emg.raw.measurements.v2",
        "manifest_artifact_id": source["artifact_id"], "raw_export_artifact_id": raw_ref["artifact_id"],
        "processed_artifact_id": processed_ref["artifact_id"], "method_artifact_id": method_ref["artifact_id"],
        "sample_count": count, "sample_rate_hz": rate, "device": data["device"],
        "time_qc": {"missing_timestamps": sum(row[2] == "missing" for row in rows), "nonfinite_timestamps": sum(row[2] == "nonfinite" for row in rows), "rate_gap_count": sum(reason == "sample_gap_or_rate_conflict" for reason in timing_bad)},
        "channel_qc": channel_qc, "measurements": measurements,
        "mvc_reference": None, "normalized_activation": None, "side_comparison": None,
        "task_events_source": "recorded_manifest" if data["events"] else None,
        "task_phases_assessed": bool(data["events"]),
        "comparison_metadata": comparison,
    }
    summary_ref = add_json(context, summary)
    observations = []
    for item in measurements:
        local = {**data, "anatomy": {**data["anatomy"], "side": item["channel"]["side"]}}
        local["acquired_at"] = _stamp(data["acquired_at"], item["start_s"])
        evidence = [
            {"artifact_id": raw_ref["artifact_id"], "selector": {"kind": "sample_interval", "channel_id": item["channel"]["id"], "source_column": item["channel"]["column"], "sample_start": item["sample_start"], "sample_stop_exclusive": item["sample_stop_exclusive"]}},
            {"artifact_id": summary_ref["artifact_id"], "selector": {"kind": "measurement", "measurement_id": item["id"]}},
            {"artifact_id": method_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/"}},
            {"artifact_id": source["artifact_id"], "selector": {"kind": "channel_acquisition", "channel_id": item["channel"]["id"], "event_id": item["event"]["id"] if item["event"] else None}},
            {"artifact_id": processed_ref["artifact_id"], "selector": {"kind": "sample_interval", "channel_id": item["channel"]["id"], "sample_start": item["sample_start"], "sample_stop_exclusive": item["sample_stop_exclusive"]}},
            {"artifact_id": previews[1]["artifact_id"], "selector": {"kind": "image", "channel_id": item["channel"]["id"], "frame_index": 0}},
        ]
        for concept, key, unit, method_name in (
            ("emg.rms", "rms_uV", "uV", "scipy_contiguous_filtered_rms_v2"),
            ("emg.envelope", "envelope_mean_uV", "uV", "scipy_centered_rectified_moving_mean_envelope_window_mean_v2"),
            ("emg.median_frequency", "median_frequency_Hz", "Hz", "scipy_welch_periodic_hann_discrete_median_v2"),
        ):
            value = item[key]
            quality = item["quality"] if value is not None else "unreliable" if item["quality"] == "unreliable" or item["channel"]["calibration"]["status"] == "invalid" else "unknown"
            observations.append(observation(request, local, concept, item["id"], value, unit, quality, method_name, evidence))
    partial = not observations or any(item["value"] is None or item["quality"] != "usable" for item in observations)
    warnings = [{"code": "surface_emg_descriptive_only", "message": "Surface RMS, envelope and median frequency are descriptive measurements; they do not diagnose radiculopathy, pain cause or normal muscle function."}]
    if partial:
        warnings.append({"code": "raw_emg_quality_or_metadata_incomplete", "message": "Some windows or physical units are not assessed, or signal quality is unknown. See the derived QC and method artifacts."})
    if not data["events"]:
        warnings.append({"code": "task_phases_unknown", "message": "No recorded task intervals were supplied; windows belong to the whole recording and are not assigned invented task phases."})
    return validate_result(result_envelope(request, observations, [source, raw_ref, processed_ref, *previews, method_ref, summary_ref], "partial" if partial else "complete", warnings), request)
