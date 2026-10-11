"""Strict acquisition manifest for the versioned raw FLIR profile."""
from datetime import datetime
import math

from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError

CONDITION_TAGS = {
    "emissivity": "Emissivity", "distance_m": "ObjectDistance",
    "reflected_Cel": "ReflectedApparentTemperature", "ambient_Cel": "AtmosphericTemperature",
    "window_Cel": "IRWindowTemperature", "window_transmission": "IRWindowTransmission",
    "relative_humidity_fraction": "RelativeHumidity",
}
MANIFEST_FIELDS = {
    "schema_version", "source_kind", "jpeg_artifact_id", "acquisition_mode", "origin",
    "subject_ref", "session_ref", "protocol_id", "acquired_at", "clock_domain", "pose",
    "coordinate_frame", "count_byte_order", "calibration", "conditions", "source_provenance",
}


def require_object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ContractError("Raw thermography profile requires exactly the documented fields")
    return value


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise ContractError("Raw thermography profile requires bounded nonempty text")
    return value


def number(value):
    if type(value) not in (int, float):
        raise ContractError("Radiometry requires finite numbers")
    try:
        result = float(value)
    except OverflowError:
        raise ContractError("Radiometry requires finite numbers") from None
    if not math.isfinite(result):
        raise ContractError("Radiometry requires finite numbers")
    return result


def validate_condition(key, value):
    value = number(value)
    if key in {"emissivity", "window_transmission"} and not 0 < value <= 1:
        raise ContractError("Radiometry transmission and emissivity must be in (0, 1]")
    if key == "relative_humidity_fraction" and not 0 <= value <= 1:
        raise ContractError("Radiometry humidity must be a fraction in [0, 1]")
    if key == "distance_m" and not 0 <= value <= 10000:
        raise ContractError("Radiometry distance is outside the supported domain")
    if key.endswith("_Cel") and not -273.15 < value < 10000:
        raise ContractError("Radiometry Celsius settings are outside the physical domain")
    return value


def validate_manifest(data, jpeg):
    require_object(data, MANIFEST_FIELDS)
    if data["schema_version"] != "1.0" or data["source_kind"] != "thermography-raw-flir.v1":
        raise ContractError("Unsupported raw thermography profile")
    if data["jpeg_artifact_id"] != jpeg["artifact_id"]:
        raise ContractError("Raw thermography manifest must bind the uploaded JPEG artifact")
    if (data["acquisition_mode"], data["origin"]) not in {
        ("recorded", "automatically_measured"), ("synthetic", "synthetic_demo"),
    }:
        raise ContractError("Unsupported raw thermography acquisition/origin")
    for key in ("subject_ref", "session_ref", "protocol_id", "clock_domain"):
        if data[key] is not None:
            opaque_id(data[key])
    for key in ("pose", "coordinate_frame"):
        if data[key] is not None:
            text(data[key])
    if data["coordinate_frame"] is None:
        raise ContractError("Raw thermography requires its native pixel coordinate frame")
    stamp = data["acquired_at"]
    if stamp is not None:
        text(stamp)
        try:
            parsed = datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
        except ValueError:
            raise ContractError("Raw thermography source timestamp is invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ContractError("Raw thermography source timestamp requires a known timezone")
    if data["count_byte_order"] not in {"little", "big"}:
        raise ContractError("Raw thermography PNG count byte order must be declared")
    calibration = require_object(data["calibration"], {"status", "source", "author_id"})
    if calibration["status"] not in {"verified", "unknown", "invalid"}:
        raise ContractError("Unsupported raw thermography calibration status")
    if calibration["source"] is not None:
        text(calibration["source"])
    if calibration["author_id"] is not None:
        opaque_id(calibration["author_id"])
    if calibration["status"] == "verified" and any(calibration[key] is None for key in ("source", "author_id")):
        raise ContractError("Verified raw thermography calibration requires its source and reviewer")
    conditions = require_object(data["conditions"], set(CONDITION_TAGS) | {"status", "source"})
    if conditions["status"] not in {"file_settings_only", "independently_verified", "unknown"}:
        raise ContractError("Unsupported raw thermography conditions status")
    if conditions["source"] is not None:
        text(conditions["source"])
    if any(conditions[key] is not None for key in CONDITION_TAGS) and conditions["source"] is None:
        raise ContractError("Explicit physical settings require a declared source")
    if conditions["status"] == "independently_verified" and (
        conditions["source"] is None or any(conditions[key] is None for key in CONDITION_TAGS)
    ):
        raise ContractError("Verified physical conditions require every documented setting and source")
    for key in CONDITION_TAGS:
        if conditions[key] is not None:
            validate_condition(key, conditions[key])
    provenance = require_object(data["source_provenance"], {
        "source_url", "source_revision", "license_status", "license_identifier",
    })
    for key in ("source_url", "source_revision", "license_identifier"):
        if provenance[key] is not None:
            text(provenance[key])
    if provenance["license_status"] not in {"unknown", "permitted", "synthetic"}:
        raise ContractError("Unsupported source license status")
    if provenance["license_status"] == "permitted" and provenance["license_identifier"] is None:
        raise ContractError("Permitted data require a license or permission reference")
    if data["acquisition_mode"] == "recorded" and provenance["license_status"] == "synthetic":
        raise ContractError("Recorded data cannot claim a synthetic data license")
    return data
