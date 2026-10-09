"""Only technical operations: lineage, time windows, calibration and frames."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_platform.comparison import compare_snapshot
from dorsopathy_platform.results import observation_id_for, result_envelope
from dorsopathy_platform.storage import ArtifactStore
from test_snapshot_adapter import EPISODE, make_snapshot, make_manifest, make_attestation, ReadOnlyCatalog


def comparison_fixture(store, module_id, origin="synthetic_demo", changes=None, extra_refs=None):
    source = store.add_bytes(canonical_bytes({"origin": origin, "calibration": {"mm_per_unit": 1}}), "application/json")
    metadata = {"schema_version": "1.0", "subject_ref": "fixture-subject", "session_ref": "fixture-session", "protocol_id": "fixture-protocol",
                "linkage_evidence_refs": [], "acquired_interval": {"start": "2026-10-09T10:00:00+03:00", "end": "2026-10-09T10:01:00+03:00", "clock_domain": "fixture-clock"},
                "event_id": "event-one", "pose_id": "standing", "calibration_status": "verified",
                "frame": {"id": "fixture-common-frame", "units": "mm", "axes": ["X", "Y", "Z"]}, "transform_refs": [],
                "calibration_evidence_refs": [{"artifact_id": source["artifact_id"], "selector": {"kind": "json", "pointer": "/calibration"}}]}
    metadata.update(changes or {})
    sidecar = store.add_bytes(canonical_bytes({"comparison_metadata": metadata, "source_artifact_id": source["artifact_id"]}), "application/json", role="derived")
    request = {"schema_version": "1.0", "job_id": module_id + "-job", "episode_id": EPISODE,
               "module_id": module_id, "module_version": "0.1.0", "input_artifacts": [source], "parameters": {}, "protocol_id": None}
    observation = {"observation_id": observation_id_for(request["job_id"], "fixture.distance"), "concept": "fixture.distance",
                   "assessment_status": "assessed", "value": 10, "unit": "mm", "anatomy": {"region": "lower_back", "side": "midline", "level": None, "numbering_status": "not_applicable"},
                   "quality": "usable", "origin": origin, "method": "synthetic-geometry", "acquired_at": "2026-10-09T10:00:00+03:00", "coordinate_frame": "fixture-common-frame",
                   "evidence_refs": [{"artifact_id": sidecar["artifact_id"], "selector": {"kind": "json", "pointer": "/comparison_metadata"}}], "model": None}
    return result_envelope(request, [observation], [source, sidecar] + (extra_refs or []))


class MultimodalComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))

    def pair_for(self, left, right, manifest=None, reader=None):
        report = compare_snapshot(make_snapshot([left, right]), manifest or make_manifest([left, right]), reader or self.store)
        self.assertEqual(len(report["pairs"]), 1)
        return report["pairs"][0]

    @staticmethod
    def operation(pair, kind):
        return next(operation for operation in pair["operations"] if operation["kind"] == kind)

    def test_aligned_synthetic_fixture_has_operation_reasons_evidence_and_no_score(self):
        pair = self.pair_for(comparison_fixture(self.store, "fixture.left"), comparison_fixture(self.store, "fixture.right"))
        self.assertEqual(pair["status"], "compatible")
        self.assertEqual({operation["status"] for operation in pair["operations"]}, {"compatible"})
        self.assertTrue(all(operation["evidence_refs"] and operation["reasons"] for operation in pair["operations"]))
        self.assertNotIn("score", pair)
        self.assertNotIn("probability", pair)

    def test_shifted_time_different_event_and_pose_are_explicit(self):
        left = comparison_fixture(self.store, "fixture.left")
        for changes, reason in (({"acquired_interval": {"start": "2026-10-09T11:00:00+03:00", "end": "2026-10-09T11:01:00+03:00", "clock_domain": "fixture-clock"}}, "acquisition_windows_do_not_overlap"),
                                ({"event_id": "other-event"}, "selected_events_differ"), ({"pose_id": "supine"}, "selected_poses_differ")):
            pair = self.pair_for(left, comparison_fixture(self.store, "fixture.right", changes=changes))
            temporal = self.operation(pair, "event_window")
            self.assertEqual(temporal["status"], "incompatible")
            self.assertIn(reason, temporal["reasons"])

    def test_missing_calibration_unknown_clock_and_different_frames_stay_unknown(self):
        left = comparison_fixture(self.store, "fixture.left")
        for changes, kind, reason in (({"calibration_evidence_refs": []}, "spatial_overlay", "calibration_evidence_missing"),
                                     ({"acquired_interval": {"start": None, "end": None, "clock_domain": None}}, "event_window", "acquisition_time_unknown"),
                                     ({"frame": {"id": "different-frame", "units": "mm", "axes": ["X", "Y", "Z"]}}, "spatial_overlay", "validated_transform_missing"),
                                     ({"frame": {"id": "image-frame", "units": "px", "axes": ["X", "Y"]}}, "spatial_overlay", "metric_frame_or_projection_unsupported")):
            pair = self.pair_for(left, comparison_fixture(self.store, "fixture.right", changes=changes))
            operation = self.operation(pair, kind)
            self.assertEqual(operation["status"], "unknown")
            self.assertIn(reason, operation["reasons"])

    def test_unknown_linkage_and_explicit_subject_mismatch_never_become_compatible(self):
        left = comparison_fixture(self.store, "fixture.left")
        right = comparison_fixture(self.store, "fixture.right", changes={"subject_ref": "another-subject"})
        pair = self.pair_for(left, right)
        self.assertEqual(pair["status"], "incompatible")
        self.assertTrue(all(operation["reasons"] == ["source_subjects_differ"] for operation in pair["operations"]))
        manifest = make_manifest([left, right], "replay", "unlinked")
        pair = self.pair_for(left, right, manifest)
        self.assertEqual(pair["status"], "unknown")

    def test_recorded_indices_do_not_gain_verified_clocks_from_wall_time_strings(self):
        left = comparison_fixture(self.store, "fixture.left", origin="automatically_measured")
        right = comparison_fixture(self.store, "fixture.right", origin="automatically_measured")
        attestation = make_attestation(self.store, [left, right])
        manifest = make_manifest([left, right], "replay", "unlinked")
        manifest.update(linkage="verified_linked", linkage_evidence_artifact_id=attestation["artifact_id"])
        pair = self.pair_for(left, right, manifest, ReadOnlyCatalog(self.store, [attestation]))
        temporal = self.operation(pair, "event_window")
        self.assertEqual(temporal["status"], "unknown")
        self.assertIn("clock_accuracy_unverified", temporal["reasons"])

    def test_numeric_validated_transform_supports_prerequisites_only_and_error_limit(self):
        landmarks = self.store.add_bytes(canonical_bytes({"points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]}), "application/json", role="derived")
        residual = self.store.add_bytes(canonical_bytes({"rms_mm": 0.5}), "application/json", role="derived")
        for error, expected in ((0.5, "compatible"), (3, "incompatible")):
            transform = {"schema_version": "1.0", "source_kind": "coordinate-transform.v1", "from_frame": "source-frame", "to_frame": "target-frame", "units": "mm",
                         "mapping_direction": "from_to", "matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                         "method": "fixture-landmarks", "version": "1.0", "pose_id": "standing",
                         "validation": {"status": "verified", "error_value": error, "error_unit": "mm", "max_error": 1,
                                        "landmark_evidence_refs": [{"artifact_id": landmarks["artifact_id"], "selector": {"kind": "json", "pointer": "/points"}}],
                                        "validation_evidence_refs": [{"artifact_id": residual["artifact_id"], "selector": {"kind": "json", "pointer": "/rms_mm"}}]}}
            transform_ref = self.store.add_bytes(canonical_bytes({"transform": transform}), "application/json", role="derived")
            left = comparison_fixture(self.store, "fixture.left", changes={"frame": {"id": "source-frame", "units": "mm", "axes": ["X", "Y", "Z"]},
                         "transform_refs": [{"artifact_id": transform_ref["artifact_id"], "selector": {"kind": "json", "pointer": "/transform"}}]}, extra_refs=[transform_ref, landmarks, residual])
            right = comparison_fixture(self.store, "fixture.right", changes={"frame": {"id": "target-frame", "units": "mm", "axes": ["X", "Y", "Z"]}})
            pair = self.pair_for(left, right)
            operation = self.operation(pair, "spatial_overlay")
            self.assertEqual(operation["status"], expected)
            if expected == "compatible":
                self.assertIn("not_resampled", operation["reasons"][0])
                self.assertIn("transform_ref", operation)

    def test_failed_or_unavailable_evidence_preserves_independent_unknown_pair(self):
        left, right = comparison_fixture(self.store, "fixture.left"), comparison_fixture(self.store, "fixture.right")
        left.update(status="failed", observations=[], error={"code": "failed_fixture", "message": "Unavailable"})
        pair = self.pair_for(left, right)
        self.assertTrue(all(operation["status"] == "unknown" for operation in pair["operations"]))
        left = comparison_fixture(self.store, "fixture.left")
        sidecar = left["artifacts"][1]
        (self.store.root / sidecar["storage_key"]).write_bytes(b"changed")
        pair = self.pair_for(left, right)
        self.assertIn("observation_evidence_unavailable", self.operation(pair, "semantic_region")["reasons"])

    def test_malformed_sidecar_is_unknown_without_crashing_and_late_unselected_data_ignored(self):
        left = comparison_fixture(self.store, "fixture.left")
        right = comparison_fixture(self.store, "fixture.right", changes={"acquired_interval": []})
        pair = self.pair_for(left, right)
        self.assertEqual(self.operation(pair, "event_window")["status"], "unknown")
        snapshot, manifest = make_snapshot([left, right]), make_manifest([left, right])
        original = compare_snapshot(snapshot, manifest, self.store)
        late = comparison_fixture(self.store, "late.unselected")
        updated = compare_snapshot(make_snapshot([left, right, late]), manifest, self.store)
        self.assertEqual(original["pairs"], updated["pairs"])


if __name__ == "__main__":
    unittest.main()
