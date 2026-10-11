"""Strict known PMD-200 fields, explicit time dialect, preserved row-level QC."""
import csv
from datetime import datetime, timedelta
import io
import math
import re

from dorsopathy_platform.contracts import ContractError
from .profile import HEADERS, timestamp

MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_ROWS = 32768
NUMERIC = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z", re.ASCII)
ELAPSED = re.compile(r"(\d{1,5}):([0-5]\d):([0-5]\d(?:\.\d{1,6})?)\Z", re.ASCII)
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?\Z", re.ASCII)


def numeric_cell(value, missing, low, high):
    if value in missing:
        return None, "missing"
    if NUMERIC.fullmatch(value) is None:
        return None, "invalid_numeric_token"
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None, "invalid_numeric_token"
    if not math.isfinite(result) or not low <= result <= high:
        return None, "outside_supported_domain"
    return result, None


def parse_time(value, data):
    if value in data["missing_tokens"]:
        return None, None, "missing_time"
    mode = data["time_encoding"]
    if mode == "elapsed_seconds":
        seconds, reason = numeric_cell(value, (), 0, 366 * 86400)
        return seconds, None, "invalid_time" if reason else None
    if mode == "elapsed_hh_mm_ss":
        match = ELAPSED.fullmatch(value)
        if match is None:
            return None, None, "invalid_time"
        hours, minutes, seconds = match.groups()
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return (result, None, None) if result <= 366 * 86400 else (None, None, "invalid_time")
    if ISO.fullmatch(value) is None:
        return None, None, "invalid_time"
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None, None, "invalid_time"
    return None, parsed, None


def import_csv(stream, data):
    rows, events = [], []
    try:
        # The exact four fields are documented; dialect/encoding support below
        # is a deliberately narrow adapter subset, pending a real export sample.
        wrapper = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        reader = csv.reader(wrapper, delimiter=",", quotechar='"', strict=True)
        if tuple(next(reader, [])) != HEADERS:
            raise ContractError("CSV must have the exact documented PMD-200 four-column header")
        for row_index, fields in enumerate(reader):
            if row_index >= MAX_ROWS or len(fields) != 4 or any(len(field) > 4096 or "\x00" in field for field in fields):
                raise ContractError("PMD-200 CSV dimensions or fields exceed the supported profile")
            seconds, parsed, time_reason = parse_time(fields[0], data)
            index, index_reason = numeric_cell(fields[1], data["missing_tokens"], 0, 100)
            heart, heart_reason = numeric_cell(fields[2], data["missing_tokens"], 0, 1000)
            reasons = [reason for reason in (time_reason, "nol_" + index_reason if index_reason else None,
                                           "heart_rate_" + heart_reason if heart_reason else None) if reason]
            row = {"source_row_index": row_index, "source_line_number": reader.line_num, "source_Time": fields[0],
                   "time_s": seconds, "acquired_at": None, "nol_index": index, "heart_rate_per_min": heart,
                   "raw_quality": None, "quality": "unreliable" if reasons else "unknown",
                   "quality_reason": "quality_not_in_documented_csv_export", "qc_reasons": reasons,
                   "events": [], "_parsed": parsed}
            if fields[3]:
                if len(events) >= 1024:
                    raise ContractError("PMD-200 annotations exceed the supported profile")
                event_id = "csv-annotation-" + str(row_index)
                events.append({"id": event_id, "label": fields[3], "source_row_index": row_index,
                               "time_s": None, "acquired_at": None, "time_meaning": "recorded_annotation_csv_time",
                               "physical_stimulus_onset_confirmed": False, "quality": "unknown"})
                row["events"].append(event_id)
            rows.append(row)
    except (csv.Error, UnicodeError):
        raise ContractError("PMD-200 CSV cannot be decoded in the supported UTF-8 comma-separated dialect") from None
    finally:
        if "wrapper" in locals():
            wrapper.detach()
    if not rows:
        raise ContractError("PMD-200 CSV has no data rows")
    anchor = timestamp(data["acquired_at"])
    first = next((row["_parsed"] for row in rows if row["_parsed"] is not None), None)
    clock_conflict = False
    def assign_absolute(row, source_anchor):
        try:
            row["acquired_at"] = (source_anchor + timedelta(seconds=row["time_s"])).isoformat()
        except OverflowError:
            row["qc_reasons"].append("absolute_time_overflow")
    if first is not None:
        source_is_aware = first.tzinfo is not None and first.utcoffset() is not None
        if anchor is not None:
            clock_conflict = first != anchor if source_is_aware else first != anchor.replace(tzinfo=None)
        elif source_is_aware:
            anchor = first
        for row in rows:
            parsed = row["_parsed"]
            if parsed is None:
                continue
            aware = parsed.tzinfo is not None and parsed.utcoffset() is not None
            if aware != source_is_aware:
                row["qc_reasons"].append("mixed_timezone_encoding")
                continue
            row["time_s"] = (parsed - first).total_seconds()
            if aware:
                row["acquired_at"] = parsed.isoformat()
            elif anchor is not None and not clock_conflict:
                assign_absolute(row, anchor)
    elif data["time_encoding"] != "iso8601" and anchor is not None:
        for row in rows:
            if row["time_s"] is not None:
                assign_absolute(row, anchor)
    times, previous, duplicate_count, gap_count, nonmonotonic_count, irregular_count = {}, None, 0, 0, 0, 0
    period, tolerance = data["sampling_period_s"], data["timing_tolerance_fraction"]
    for row in rows:
        time = row["time_s"]
        if clock_conflict:
            row["qc_reasons"].append("acquisition_anchor_conflict")
            row["acquired_at"] = None
        if time is not None:
            if time < 0:
                row["qc_reasons"].append("negative_relative_time")
            if time in times:
                duplicate_count += 1
                row["qc_reasons"].append("duplicate_timestamp")
                earlier = rows[times[time]]
                if "duplicate_timestamp" not in earlier["qc_reasons"]:
                    earlier["qc_reasons"].append("duplicate_timestamp")
            else:
                times[time] = row["source_row_index"]
            if previous is not None:
                interval = time - previous
                if interval < 0:
                    nonmonotonic_count += 1
                    row["qc_reasons"].append("nonmonotonic_timestamp")
                if period is not None and interval > 0 and abs(interval - period) > period * tolerance:
                    irregular_count += 1
                    row["qc_reasons"].append("irregular_cadence")
                    if interval > period * (1 + tolerance):
                        gap_count += 1
                        row["qc_reasons"].append("timestamp_gap")
            previous = time
        row.pop("_parsed")
    for row in rows:
        row["quality"] = "unreliable" if row["qc_reasons"] else "unknown"
    for event in events:
        row = rows[event["source_row_index"]]
        event["time_s"], event["acquired_at"], event["quality"] = row["time_s"], row["acquired_at"], row["quality"]
    temporal_reasons = {"missing_time", "invalid_time", "mixed_timezone_encoding", "negative_relative_time",
                        "absolute_time_overflow", "acquisition_anchor_conflict", "duplicate_timestamp",
                        "nonmonotonic_timestamp", "irregular_cadence", "timestamp_gap"}
    time_complete = all(row["time_s"] is not None and not temporal_reasons.intersection(row["qc_reasons"]) for row in rows)
    timezone_known = all(row["acquired_at"] is not None for row in rows) and not clock_conflict
    qc = {"row_count": len(rows), "quality_unknown_count": sum(row["quality"] == "unknown" for row in rows),
          "quality_unreliable_count": sum(row["quality"] == "unreliable" for row in rows), "quality_usable_count": 0,
          "missing_or_invalid_time_count": sum(row["time_s"] is None for row in rows),
          "missing_or_invalid_nol_count": sum(row["nol_index"] is None for row in rows),
          "missing_or_invalid_heart_rate_count": sum(row["heart_rate_per_min"] is None for row in rows),
          "duplicate_timestamp_count": duplicate_count, "nonmonotonic_timestamp_count": nonmonotonic_count,
          "gap_count": gap_count, "irregular_interval_count": irregular_count,
          "declared_sampling_period_s": period, "sampling_period_source": "acquisition_manifest" if period is not None else "unknown",
          "time_encoding": data["time_encoding"], "global_timezone_known": timezone_known,
          "temporal_integrity_complete": time_complete,
          "quality_exported": False, "clock_conflict": clock_conflict,
          "absolute_clock_comparability": "declared_not_independently_validated" if data["clock_domain"] is not None and timezone_known and time_complete else "unknown"}
    return rows, events, qc
