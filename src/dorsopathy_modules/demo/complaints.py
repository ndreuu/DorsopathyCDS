"""Synthetic questionnaire producer showing an independent provider boundary."""

from dorsopathy_platform.contracts import loads_json
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_platform.ports import ProviderSpec


class DemoComplaintsProvider:
    spec = ProviderSpec("demo.complaints", "0.1.0", "Учебная анкета", synthetic_only=True)

    def analyze(self, request, context):
        if len(request["input_artifacts"]) != 1:
            raise ValueError("Demo questionnaire requires one fixture")
        source = request["input_artifacts"][0]
        fixture = loads_json(context.artifacts.read(source))
        if fixture.get("synthetic") is not True:
            raise ValueError("Synthetic fixture required")
        intensity = fixture["pain_intensity"]
        if type(intensity) is not int or not 0 <= intensity <= 10:
            raise ValueError("Expected an integer questionnaire score from 0 to 10")
        observation = {
            "observation_id": observation_id_for(request["job_id"], "symptoms.pain_intensity"),
            "concept": "symptoms.pain_intensity", "assessment_status": "assessed",
            "value": intensity, "unit": "{score}",
            "anatomy": {"region": fixture["region"], "side": fixture["side"], "level": None, "numbering_status": "not_applicable"},
            "quality": "usable", "origin": "synthetic_demo", "method": "synthetic-questionnaire-fixture",
            "acquired_at": None, "coordinate_frame": None,
            "evidence_refs": [{"artifact_id": source["artifact_id"], "selector": {"field": "pain_intensity"}}],
            "model": None,
        }
        return result_envelope(request, [observation], [source])
