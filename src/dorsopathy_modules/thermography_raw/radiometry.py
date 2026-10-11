# SPDX-License-Identifier: GPL-3.0-or-later
# Radiometry equations adapted from Glenn J. Tattersall's Thermimage raw2temp
# and the local prepare_thermal.py research conversion. Upstream attribution:
# https://github.com/gtatters/Thermimage/blob/master/R/raw2temp.R
# DESCRIPTION declares GPL (>=2); upstream LICENSE.md contains GPLv3.
# This adapted component is distributed under GPL-3.0-or-later. No camera
# defaults from that implementation are used: every coefficient is file-specific.
# Modified 2026-10-10: bounded Python conversion, domain/QC checks, provenance.
# See NOTICE.md and THERMIMAGE_LICENSE.txt. This component has no warranty.
"""Validated file-specific FLIR Planck conversion to Celsius estimates."""
import math

from dorsopathy_platform.contracts import ContractError
from .decoder import RADIOMETRY_TAGS
from .profile import CONDITION_TAGS, number, validate_condition

REFERENCE = {
    "id": "thermimage-raw2temp-local-adaptation",
    "version": "1.0",
    "source": "https://github.com/gtatters/Thermimage/blob/master/R/raw2temp.R",
    "reference_sha256": "2c1744363d8f36318d06955f1004dd98329e5277a9a6fd23d725e1b4850b8916",
    "license": "GPL-3.0-or-later",
    "local_predecessor": "var/real-inputs/thermography/prepare_thermal.py",
}


def tag_number(metadata, name):
    value = metadata.get("FLIR:" + name)
    if type(value) is str:
        try:
            value = float(value)
        except ValueError:
            raise ContractError("FLIR radiometry metadata is not numeric") from None
    return number(value)


def convert_counts(counts, metadata, declared_conditions):
    if type(counts) is not list or not counts or type(counts[0]) is not list or not counts[0]:
        raise ContractError("FLIR sensor counts require a rectangular matrix")
    width = len(counts[0])
    if len(counts) > 1024 or width > 1024:
        raise ContractError("FLIR sensor count dimensions exceed the supported profile")
    for row in counts:
        if type(row) is not list or len(row) != width or any(type(value) is not int or not 0 <= value <= 65535 for value in row):
            raise ContractError("FLIR sensor counts must be rectangular uint16")
    constants = {name: tag_number(metadata, name) for name in RADIOMETRY_TAGS}
    if any(constants[name] <= 0 for name in ("PlanckR1", "PlanckR2", "PlanckB", "PlanckF")):
        raise ContractError("FLIR Planck coefficients are outside the physical domain")
    settings, setting_sources = {}, {}
    for key, tag in CONDITION_TAGS.items():
        declared = declared_conditions[key]
        settings[key] = validate_condition(key, tag_number(metadata, tag) if declared is None else declared)
        setting_sources[key] = {"kind": "file_tag", "tag": "FLIR:" + tag} if declared is None else {"kind": "acquisition_manifest", "source": declared_conditions["source"]}
    try:
        e, d, rt, at, wt, irt, rh = [settings[key] for key in CONDITION_TAGS]
        r1, r2, b, f, offset = [constants[name] for name in ("PlanckR1", "PlanckR2", "PlanckB", "PlanckF", "PlanckO")]
        a1, a2, b1, b2, mixture = [constants[name] for name in ("AtmosphericTransAlpha1", "AtmosphericTransAlpha2", "AtmosphericTransBeta1", "AtmosphericTransBeta2", "AtmosphericTransX")]
        water = rh * math.exp(1.5587 + 0.06939 * at - 0.00027816 * at ** 2 + 0.00000068455 * at ** 3)
        tau = mixture * math.exp(-math.sqrt(d / 2) * (a1 + b1 * math.sqrt(water))) + (1 - mixture) * math.exp(-math.sqrt(d / 2) * (a2 + b2 * math.sqrt(water)))
        if not math.isfinite(tau) or not 0 < tau <= 1:
            raise ContractError("FLIR atmospheric transmission is outside the physical domain")

        def radiance(temperature):
            denominator = r2 * (math.exp(b / (temperature + 273.15)) - f)
            if denominator <= 0 or not math.isfinite(denominator):
                raise ContractError("FLIR correction settings produce an invalid radiance")
            return r1 / denominator - offset

        correction = ((1 - e) / e * radiance(rt) + (1 - tau) / e / tau * radiance(at)
                      + (1 - irt) / e / tau / irt * radiance(wt)
                      + (1 - tau) / e / tau / irt / tau * radiance(at))
        scale = e * tau * irt * tau
    except (OverflowError, ValueError, ZeroDivisionError):
        raise ContractError("FLIR settings cannot be converted within the physical domain") from None
    raw_low = tag_number(metadata, "RawValueRangeMin") if "FLIR:RawValueRangeMin" in metadata else 1
    raw_high = tag_number(metadata, "RawValueRangeMax") if "FLIR:RawValueRangeMax" in metadata else 65534
    if not 0 <= raw_low < raw_high <= 65535:
        raise ContractError("FLIR declared sensor count range is invalid")
    matrix, invalid, saturated = [], 0, 0
    for row in counts:
        temperatures = []
        for raw in row:
            temperature = None
            if raw in (0, 65535) or not raw_low <= raw <= raw_high:
                saturated += 1
            else:
                try:
                    object_radiance = raw / scale - correction
                    denominator = r2 * (object_radiance + offset)
                    argument = r1 / denominator + f if denominator > 0 else 0
                    if argument > 1 and math.isfinite(argument):
                        candidate = b / math.log(argument) - 273.15
                        if math.isfinite(candidate) and candidate > -273.15:
                            temperature = candidate
                except (OverflowError, ValueError, ZeroDivisionError):
                    pass
            if temperature is None:
                invalid += 1
            temperatures.append(temperature)
        matrix.append(temperatures)
    if invalid == width * len(counts):
        raise ContractError("FLIR radiometry produced no valid Celsius pixels")
    return matrix, {"coefficients": constants, "conditions_used": settings,
                    "condition_sources": setting_sources, "conditions_status": declared_conditions["status"],
                    "atmospheric_transmission": tau, "conversion": REFERENCE,
                    "raw_valid_range": [raw_low, raw_high], "invalid_pixel_count": invalid,
                    "saturated_or_outside_range_pixel_count": saturated}
