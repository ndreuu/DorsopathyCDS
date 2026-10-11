"""Strict acquisition profile for a documented four-column PMD-200 export."""
from datetime import datetime
import math

from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError

PROFILE = "medasense-pmd200-summary-four-columns.v1"
HEADERS = ("Time", "NOL index", "Heart rate", "Annotations")
MANUAL_URL = "https://medasense.com/wp-content/uploads/2025/11/Medasense-PMD-200-User-manual-2.2.1-MK2U-02-708-English-US.pdf"
FDA_URL = "https://www.accessdata.fda.gov/cdrh_docs/reviews/DEN210022.pdf"
MANIFEST_FIELDS = {
    "schema_version", "source_kind", "csv_artifact_id", "vendor_profile", "device",
    "acquisition_mode", "origin", "subject_ref", "session_ref", "protocol_id",
    "clinical_context", "acquired_at", "clock_domain", "time_encoding",
    "sampling_period_s", "timing_tolerance_fraction", "missing_tokens",
    "calibration", "response_windows", "source_provenance",
}


def require(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ContractError("Nociception acquisition profile requires exactly documented fields")
    return value


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise ContractError("Nociception profile requires bounded nonempty text")
    return value


def number(value):
    if type(value) not in (int, float):
        raise ContractError("Nociception metadata requires finite numbers")
    try:
        result = float(value)
    except OverflowError:
        raise ContractError("Nociception metadata requires finite numbers") from None
    if not math.isfinite(result):
        raise ContractError("Nociception metadata requires finite numbers")
    return result


def choice(value, allowed):
    if type(value) is not str or value not in allowed:
        raise ContractError("Unsupported nociception acquisition profile value")


def timestamp(value):
    if value is None:
        return None
    text(value)
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ContractError("Nociception acquisition timestamp is invalid") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("Nociception acquisition timestamp requires a known source timezone")
    return result


def validate_manifest(data, csv_ref):
    require(data, MANIFEST_FIELDS)
    if data["schema_version"] != "1.0" or data["source_kind"] != "nociception-vendor-csv.v2" or data["vendor_profile"] != PROFILE:
        raise ContractError("Unsupported nociception CSV profile")
    if data["csv_artifact_id"] != csv_ref["artifact_id"]:
        raise ContractError("Nociception manifest must bind its uploaded CSV")
    device = require(data["device"], {"manufacturer", "model", "software_version", "index_averaging_window_s"})
    if device["manufacturer"] != "Medasense" or device["model"] != "PMD-200":
        raise ContractError("This nociception adapter is limited to the documented PMD-200 summary fields")
    if device["software_version"] is not None:
        text(device["software_version"])
    if device["index_averaging_window_s"] is not None and not 0 < number(device["index_averaging_window_s"]) <= 86400:
        raise ContractError("Nociception index averaging window is outside the supported domain")
    choice(data["acquisition_mode"], {"recorded", "synthetic"})
    choice(data["origin"], {"automatically_measured", "synthetic_demo"})
    if (data["acquisition_mode"], data["origin"]) not in {("recorded", "automatically_measured"), ("synthetic", "synthetic_demo")}:
        raise ContractError("Nociception acquisition mode and origin disagree")
    for key in ("subject_ref", "session_ref", "protocol_id", "clock_domain"):
        if data[key] is not None:
            opaque_id(data[key])
    choice(data["clinical_context"], {"awake", "general_anaesthesia", "unknown"})
    timestamp(data["acquired_at"])
    choice(data["time_encoding"], {"elapsed_seconds", "elapsed_hh_mm_ss", "iso8601"})
    if data["sampling_period_s"] is not None and not 0 < number(data["sampling_period_s"]) <= 86400:
        raise ContractError("Nociception sampling period is outside the supported domain")
    if not 0 <= number(data["timing_tolerance_fraction"]) < .5:
        raise ContractError("Nociception timing tolerance is outside the supported domain")
    tokens = data["missing_tokens"]
    if type(tokens) is not list or not 1 <= len(tokens) <= 8 or any(type(token) is not str or len(token) > 32 for token in tokens) or len(set(tokens)) != len(tokens):
        raise ContractError("Nociception missing tokens must be a bounded explicit unique list")
    for token in tokens:
        try:
            float(token)
        except ValueError:
            continue
        raise ContractError("Numeric strings cannot be declared missing values")
    calibration = require(data["calibration"], {"status", "source", "author_id"})
    choice(calibration["status"], {"verified", "unknown", "invalid"})
    if calibration["source"] is not None:
        text(calibration["source"])
    if calibration["author_id"] is not None:
        opaque_id(calibration["author_id"])
    if calibration["status"] == "verified" and any(calibration[key] is None for key in ("source", "author_id")):
        raise ContractError("Verified device calibration requires its source and reviewer")
    windows = require(data["response_windows"], {"baseline_s", "response_s"})
    if any(not 0 < number(windows[key]) <= 3600 for key in windows):
        raise ContractError("Event-response windows exceed the supported profile")
    provenance = require(data["source_provenance"], {"source_url", "source_revision", "license_status", "license_identifier"})
    for key in ("source_url", "source_revision", "license_identifier"):
        if provenance[key] is not None:
            text(provenance[key])
    choice(provenance["license_status"], {"unknown", "permitted", "synthetic"})
    if provenance["license_status"] == "permitted" and provenance["license_identifier"] is None:
        raise ContractError("Permitted nociception data require a license/permission reference")
    if data["acquisition_mode"] == "recorded" and provenance["license_status"] == "synthetic":
        raise ContractError("Recorded nociception data cannot claim a synthetic source license")
    return data
