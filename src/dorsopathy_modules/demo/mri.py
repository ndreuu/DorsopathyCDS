"""Synthetic MRI observation producer; it never loads or analyzes an MRI."""

import math

from dorsopathy_platform.contracts import loads_json
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_platform.ports import ProviderSpec


class DemoMRIProvider:
    spec = ProviderSpec("demo.mri_anatomy", "0.1.0", "Учебное MRI-наблюдение", synthetic_only=True)

    def analyze(self, request, context):
        if len(request["input_artifacts"]) != 1:
            raise ValueError("Demo MRI provider requires one fixture")
        source = request["input_artifacts"][0]
        fixture = loads_json(context.artifacts.read(source))
        if fixture.get("synthetic") is not True:
            raise ValueError("Synthetic fixture required")
        area = fixture["area_mm2"]
        if type(area) not in (int, float) or not math.isfinite(area) or area <= 0:
            raise ValueError("Expected a finite positive area")
        observation = {
            "observation_id": observation_id_for(request["job_id"], "spine.canal.cross_sectional_area"),
            "concept": "spine.canal.cross_sectional_area", "assessment_status": "assessed",
            "value": area, "unit": "mm2",
            "anatomy": {"region": "lumbar_spine", "side": "midline", "level": fixture["level"], "numbering_status": "needs_confirmation"},
            "quality": "usable", "origin": "synthetic_demo", "method": "synthetic-area-fixture",
            "acquired_at": None, "coordinate_frame": None,
            "evidence_refs": [{"artifact_id": source["artifact_id"], "selector": {"field": "area_mm2"}}],
            "model": None,
        }
        return result_envelope(request, [observation], [source], warnings=[{
            "code": "synthetic_fixture", "message": "This is an educational value, not an MRI measurement.",
        }])
