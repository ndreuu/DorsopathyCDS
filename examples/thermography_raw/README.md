# Raw thermography examples

Only acquisition manifests and trusted worker configuration are tracked here.
`public-back-acquisition.json` refers to the public FLIR E4 study already stored
under ignored `var/real-inputs/thermography/raw/`. Its redistribution rights are
unknown. Neither that image nor its extracted matrices or derived images belong
in Git or a redistributed/clinical dataset.

`bind-on-import` is a runner template token. The runner replaces it with the
actual uploaded JPEG ArtifactRef ID and saves `acquisition-bound.json`; providers
and ordinary uploads require that real ID. `count_byte_order=little` applies to
the reviewed FLIR E4 PNG export; verify this setting for a different device.

```sh
PYTHONPATH=src python3 scripts/run_thermography_raw.py \
  --jpeg var/real-inputs/thermography/raw/FLIR2220.jpg \
  --manifest examples/thermography_raw/public-back-acquisition.json \
  --output var/real-inputs/thermography/automatic-raw \
  --exiftool /absolute/trusted/exiftool --perl /usr/bin/perl
```

The worker receives the original JPEG and acquisition manifest, extracts its
own sensor image, computes Celsius and selects the thermal ROI automatically.
No prior decoded profile, saved source.json or manually chosen rectangle is used.
Output is exploratory `gross_thermal_foreground`; mean/max do not establish a
skin segmentation, lumbar level, inflammation, radiculopathy or source of pain.

See [the raw provider documentation](../../docs/thermography_raw_ru.md).
