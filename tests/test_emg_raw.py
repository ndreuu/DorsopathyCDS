from copy import deepcopy
import gzip
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dorsopathy_modules.emg_surface import EMGSurfaceProvider, RawEMGProvider, RAW_EMG_SPEC
from dorsopathy_modules.emg_surface.raw_analysis import analyze_raw, validate_manifest
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]
HAS_SCIENCE = importlib.util.find_spec("scipy") is not None and importlib.util.find_spec("numpy") is not None


def manifest():
    return json.loads((ROOT / "examples/emg_raw/synthetic-acquisition.json").read_text())


def signal_csv(count=4096, mutate=None, amplitude=10, offset=0):
    stream = io.StringIO()
    stream.write("time_s,left_es,right_es\n")
    for index in range(count):
        time = index / 1000
        value = offset + amplitude * math.sin(2 * math.pi * 125 * time)
        row = [str(time), str(value), str(value)]
        if mutate:
            mutate(index, row)
        stream.write(",".join(row) + "\n")
    return stream.getvalue().encode()


class RawManifestTests(unittest.TestCase):
    def test_versions_are_separate_and_import_does_not_load_scipy(self):
        self.assertEqual(EMGSurfaceProvider.spec.module_version, "0.1.0")
        self.assertEqual(RAW_EMG_SPEC.module_version, "0.2.0")
        script = "import sys;from dorsopathy_modules.emg_surface import RawEMGProvider;assert 'scipy' not in sys.modules and 'numpy' not in sys.modules"
        subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)

    def test_verified_amplitudes_require_documented_per_channel_gain(self):
        data = manifest()
        validate_manifest(data)
        data["channels"][0]["calibration"]["uV_per_unit"] = None
        with self.assertRaises(ContractError):
            validate_manifest(data)

    def test_invalid_nyquist_padding_events_and_manual_metrics_rejected(self):
        for key, value in (("bandpass_hz", [20, 500]), ("edge_guard_samples", 0), ("envelope_samples", 50)):
            data = manifest()
            data["processing"][key] = value
            with self.assertRaises(ContractError):
                validate_manifest(data)
        data = manifest()
        data["events"] = [{"id": "a", "label": "task", "start_s": 1, "stop_s": 2, "source": None}]
        with self.assertRaises(ContractError):
            validate_manifest(data)
        data = manifest()
        data["rms_uV"] = 42
        with self.assertRaises(ContractError):
            validate_manifest(data)

    def test_missing_rate_is_accepted_only_without_frequency_filters(self):
        data = manifest()
        data["sample_rate_hz"] = None
        with self.assertRaises(ContractError):
            validate_manifest(data)
        data["processing"].update(bandpass_hz=None, filter_pad_samples=0, edge_guard_samples=0)
        self.assertIsNone(validate_manifest(data))


@unittest.skipUnless(HAS_SCIENCE, "Optional EMG worker NumPy/SciPy environment is required")
class RawEMGScienceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ArtifactStore(Path(self.directory.name) / "artifacts")

    def execute(self, data=None, signal=None, use_process=False):
        data = deepcopy(data or manifest())
        raw = self.store.add_bytes(signal if signal is not None else signal_csv(), "application/gzip" if data["export"]["format"] != "csv" else "text/csv")
        data["csv_artifact_id"] = raw["artifact_id"]
        source = self.store.add_bytes(json.dumps(data, allow_nan=False).encode(), "application/json")
        request = {
            "schema_version": "1.0", "job_id": "raw-emg-test", "episode_id": "synthetic-raw-emg",
            "module_id": "emg.surface", "module_version": "0.2.0", "input_artifacts": [source, raw],
            "parameters": {}, "protocol_id": data["protocol_id"],
        }
        if use_process:
            provider = RawEMGProvider(workspace_root=Path(self.directory.name) / "workers")
            self.addCleanup(provider.close)
            result = provider.analyze(request, ProviderContext(self.store))
        else:
            result = analyze_raw(request, ProviderContext(self.store))
        summaries = [json.loads(self.store.read(ref)) for ref in result["artifacts"] if ref["media_type"] == "application/json" and ref["role"] == "derived"]
        summary = next((value for value in summaries if value.get("source_kind") == "emg.raw.measurements.v2"), None)
        return result, summary

    @staticmethod
    def unfiltered(data):
        data["processing"].update(detrend="none", bandpass_hz=None, filter_pad_samples=0, edge_guard_samples=0, envelope_samples=1)

    def test_actual_subprocess_known_signal_matches_analytic_rms_and_mdf(self):
        data = manifest()
        self.unfiltered(data)
        result, summary = self.execute(data, use_process=True)
        self.assertEqual(result["status"], "complete")
        for item in summary["measurements"]:
            self.assertAlmostEqual(item["rms_uV"], math.sqrt(50), places=10)
            self.assertAlmostEqual(item["median_frequency_Hz"], 125, places=10)
            expected = sum(abs(10 * math.sin(2 * math.pi * 125 * index / 1000)) for index in range(8)) / 8
            self.assertAlmostEqual(item["envelope_mean_uV"], expected, places=10)
        self.assertIsNone(summary["side_comparison"])
        self.assertTrue(all(item["origin"] == "synthetic_demo" for item in result["observations"]))

    def test_filter_rejects_out_of_band_tones_and_retains_calibrated_in_band_tone(self):
        data = manifest()
        signal = signal_csv(amplitude=0)
        lines = signal.decode().splitlines()
        for index in range(4096):
            time = index / 1000
            value = 20 + 50 * math.sin(2 * math.pi * 5 * time) + 10 * math.sin(2 * math.pi * 125 * time) + 30 * math.sin(2 * math.pi * 490 * time)
            lines[index + 1] = f"{time},{value},{value}"
        result, summary = self.execute(data, ("\n".join(lines) + "\n").encode())
        usable = [item for item in summary["measurements"] if item["quality"] == "usable"]
        self.assertGreater(len(usable), 8)
        for item in usable:
            self.assertAlmostEqual(item["rms_uV"], math.sqrt(50), delta=0.02)
            self.assertEqual(item["median_frequency_Hz"], 125)
        self.assertEqual(result["status"], "partial")
        self.assertIn("processing_edge_guard", summary["measurements"][0]["qc_reasons"])

    def test_missing_nonfinite_clipped_and_timestamp_gaps_are_not_interpolated(self):
        data = manifest()
        self.unfiltered(data)
        def mutate(index, row):
            if index == 200:
                row[1] = "nan"
            elif index == 800:
                row[1] = "inf"
            elif index == 1400:
                row[1] = "100"
            elif index == 2048:
                row[0] = str(index / 1000 + 0.0001)
        result, summary = self.execute(data, signal_csv(mutate=mutate))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(summary["channel_qc"][0]["nonfinite_signal_samples"], 1)
        self.assertEqual(summary["channel_qc"][0]["missing_signal_samples"], 1)
        self.assertEqual(summary["channel_qc"][0]["clipped_samples"], 1)
        self.assertEqual(summary["time_qc"]["rate_gap_count"], 2)
        self.assertIsNone(summary["measurements"][0]["rms_uV"])
        self.assertAlmostEqual(summary["measurements"][1]["rms_uV"], math.sqrt(50))
        self.assertTrue(any(item["rms_uV"] is not None for item in summary["measurements"] if item["sample_start"] > 2560))

    def test_unknown_calibration_keeps_hz_and_blocks_physical_amplitudes(self):
        data = manifest()
        self.unfiltered(data)
        data["channels"][0]["calibration"] = {"status": "unknown", "source": None, "author_id": None, "uV_per_unit": None}
        data["channels"][0]["adc_rails"] = None
        result, summary = self.execute(data)
        left = summary["measurements"][0]
        self.assertIsNone(left["rms_uV"])
        self.assertIsNone(left["envelope_mean_uV"])
        self.assertEqual(left["median_frequency_Hz"], 125)
        self.assertEqual(left["quality"], "unknown")
        self.assertIsNone(summary["mvc_reference"])
        self.assertIsNone(summary["normalized_activation"])

    def test_each_channel_uses_its_own_declared_gain(self):
        data = manifest()
        self.unfiltered(data)
        data["channels"][0]["calibration"]["uV_per_unit"] = 2
        data["channels"][1]["calibration"]["uV_per_unit"] = 4
        result, summary = self.execute(data)
        self.assertAlmostEqual(summary["measurements"][0]["rms_uV"], 2 * math.sqrt(50))
        self.assertAlmostEqual(summary["measurements"][1]["rms_uV"], 4 * math.sqrt(50))
        self.assertEqual(summary["measurements"][0]["median_frequency_Hz"], 125)

    def test_unknown_rate_blocks_mdf_without_inventing_frequency(self):
        data = manifest()
        self.unfiltered(data)
        data["sample_rate_hz"] = None
        result, summary = self.execute(data)
        self.assertAlmostEqual(summary["measurements"][0]["rms_uV"], math.sqrt(50))
        self.assertIsNone(summary["measurements"][0]["median_frequency_Hz"])
        self.assertEqual(summary["measurements"][0]["quality"], "unknown")

    def test_recorded_events_and_source_sample_indices_are_preserved(self):
        data = manifest()
        self.unfiltered(data)
        data["events"] = [{"id": "measured-task", "label": "recorded acquisition annotation", "start_s": 1.024, "stop_s": 3.072, "source": "recorded task log, synthetic fixture"}]
        result, summary = self.execute(data)
        self.assertTrue(summary["task_phases_assessed"])
        self.assertEqual(len(summary["measurements"]), 8)
        self.assertEqual(summary["measurements"][0]["sample_start"], 1024)
        self.assertEqual(summary["measurements"][-1]["sample_stop_exclusive"], 3072)
        self.assertTrue(all(item["event"]["id"] == "measured-task" for item in summary["measurements"]))
        data["events"][0]["stop_s"] = 5
        with self.assertRaises(ContractError):
            self.execute(data)

    def test_gzip_csv_import_and_bad_headers(self):
        data = manifest()
        self.unfiltered(data)
        data["export"]["format"] = "csv_gzip"
        result, summary = self.execute(data, gzip.compress(signal_csv()))
        self.assertEqual(summary["sample_count"], 4096)
        with self.assertRaises(ContractError):
            self.execute(data, gzip.compress(signal_csv().replace(b"time_s,left_es,right_es", b"time_s,left_es,left_es")))

    def test_zero_power_is_unreliable_and_no_normal_or_diagnostic_value(self):
        data = manifest()
        self.unfiltered(data)
        result, summary = self.execute(data, signal_csv(amplitude=0))
        self.assertEqual(summary["measurements"][0]["rms_uV"], 0)
        self.assertIsNone(summary["measurements"][0]["median_frequency_Hz"])
        self.assertEqual(summary["measurements"][0]["quality"], "unreliable")
        self.assertEqual({item["concept"] for item in result["observations"]}, {"emg.rms", "emg.envelope", "emg.median_frequency"})


if __name__ == "__main__":
    unittest.main()
