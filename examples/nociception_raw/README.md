# Synthetic PMD-200 documented-field adapter example

These six rows are invented software fixtures, not patient measurements and not a downloaded vendor export. Their manifest requires `acquisition_mode=synthetic`, `origin=synthetic_demo`, and `license_status=synthetic`. The provider never promotes their unknown quality to usable.

Run the real isolated worker from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/run_nociception_raw.py \
  --csv examples/nociception_raw/synthetic-pmd200.csv \
  --manifest examples/nociception_raw/synthetic-acquisition.json \
  --output var/real-inputs/nociception/synthetic-vendor-import
```

Expected descriptive sample means: NOL 45 (`1`), heart rate 78 (`/min`). The recorded annotation is at 3 seconds. Event response remains unassessed; quality is unknown; no pain or dorsopathy inference is made. Result status is `partial`.

`acquisition.template.json` is an **incomplete recorded-input template**, not a valid ready-to-run acquisition claim. Select a verified `time_encoding` before use: the template's `null` deliberately fails validation. Preserve unknown cadence, averaging window, clock/timezone, calibration and clinical context. Do not relabel these synthetic rows as recorded data.

The [official manufacturer manual, page 55](https://medasense.com/wp-content/uploads/2025/11/Medasense-PMD-200-User-manual-2.2.1-MK2U-02-708-English-US.pdf) documents four fields but does not establish every byte-level CSV/timestamp detail. A legally usable unchanged real export must still validate this adapter. The current profile accepts only UTF-8/BOM, comma-separated CSV, exact four documented field names and explicitly declared time dialect. See [documentation](../../docs/nociception_raw_ru.md) and [data-readiness.json](data-readiness.json).

`worker-config.example.json` uses an absolute placeholder executable. Replace it with a trusted installed Python; configure an absolute `PYTHONPATH` or install the project in that environment. Commands are application configuration, never fields in source acquisition metadata.
