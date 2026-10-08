# T13: surface EMG — research-only подбор

Проверено 2026-10-09. Только два research-файла; код/application/tests/dependencies не изменены. База `6161304de7c02960fa3d40e681ef612e90cd3e17`, branch `research/reuse-emg`. Прочитаны AGENTS/T13/T00/brief/contracts/ports. T00 transport freeze отсутствует в этой базе; proposed concepts/selectors требуют согласования. Полные URLs/revisions/licenses/gates: [candidates.json](candidates.json).

## Минимальный выбор

**stdlib csv/gzip + NumPy/SciPy + WFDB для .hea/.dat**: raw import → QC → explicit filter → RMS/windows/PSD/MDF/MNF → artifacts/evidence, без training. HipBridge — первый небольшой back-muscle importer с unknown-unit gate; Hyser — маленький generic WFDB/RMS candidate. Shearer наиболее содержателен для lumbar/MVC, но требует выделения малого export.

| Компонент / code license | Exact entrypoint / назначение |
|---|---|
| NumPy/SciPy, BSD-3-Clause | `np.isfinite`, `sliding_window_view`; `scipy.signal.butter`, `sosfiltfilt`, `welch`: численные QC/filter/windows/PSD |
| LibEMG, MIT | `OfflineDataHandler.get_data(...)`, `parse_windows(window_size,window_increment)`, `FeatureExtractor.extract_features(...)`: optional extractor |
| NeuroKit2, MIT | `neurokit2.emg_process(emg_signal,sampling_rate=fs)`: exploratory plot/activity |
| WFDB Python, MIT | `wfdb.rdheader(record_name)`, `rdrecord(record_name,sampfrom=a,sampto=b,channels=ids,physical=True)`: scaling/fs/units/channel/sample import |

LICENSE проверены: [NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt), [SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt), [LibEMG](https://github.com/LibEMG/libemg/blob/c9b0bdc5ceadb2b36bcb2f006877e5ba1b4b974b/LICENSE), [NeuroKit](https://github.com/neuropsychology/NeuroKit/blob/ff419d983568ef492eb8d229af643c0ef0100b32/LICENSE), [WFDB](https://github.com/MIT-LCP/wfdb-python/blob/f627b5ff9dcfdb11d4c3150f9c1ebb47cfc3909d/LICENSE). Notices сохраняются; data-license отдельна.

**LibEMG risk:** [docs](https://libemg.github.io/libemg/emg_toolbox.html) дают MDF_sf, но [revision c9b0bdc](https://github.com/LibEMG/libemg/blob/c9b0bdc5ceadb2b36bcb2f006877e5ba1b4b974b/libemg/feature_extractor.py#L878) требует **MDF_fs/MNF_fs** (default1000). Exact call:
`FeatureExtractor().extract_features(["RMS","MDF","MNF"],windows,feature_dic={"MDF_fs":fs,"MNF_fs":fs},normalize=False,fix_feature_errors=False)`, shape windows×channels×samples. check_features сравнивает `==np.nan`: нужен independent isfinite. Zero-power MDF gate обязателен; fix_feature_errors заменяет ошибки нулями. Common filters имеют 60-Hz/low-fs defaults; StandardScaler не MVC.

**NeuroKit risk:** [revision ff419d9 emg_clean](https://github.com/neuropsychology/NeuroKit/blob/ff419d983568ef492eb8d229af643c0ef0100b32/neurokit2/emg/emg_clean.py#L55) forward-fills NaN/default highpass100Hz; [emg_amplitude](https://github.com/neuropsychology/NeuroKit/blob/ff419d983568ef492eb8d229af643c0ef0100b32/neurokit2/emg/emg_amplitude.py#L43) — TKEO/envelope, не RMS, public API без fs вызывает private default1000Hz. Поэтому convenience pipeline не выбран.

## Реальные back datasets

**[HipBridgeEMG v0.1](https://zenodo.org/records/17465228)**: [API](https://zenodo.org/api/records/17465228) подтверждает CC-BY-4.0/open. [README](https://zenodo.org/records/17465228/files/README.md?download=1): FREEEMG/1000Hz/SENIAM, healthy glute-bridge/prone-hip-extension trials, bilateral posterior-chain muscles, CSV.gz, 90MB. Потоково прочитаны только header+2rows [sample](https://zenodo.org/records/17465228/files/fd0f5636-phe-1.csv.gz?download=1): Frame/Time+8channels, Left/Right Erector spinae longissimus; Time0/0.001. **Units/gain/ADC-rails/MVC/exact lumbar level/timezone не объявлены**. Full-trial QC не выполнен; calibrated RMS/%MVC not_assessed, spectralHz после QC возможен.

**[Shearers v1](https://doi.org/10.26188/c.7267204.v1)**: все четыре [Figshare records](https://api.figshare.com/v2/collections/7267204/articles) CC-BY-4.0. [Статья](https://pmc.ncbi.nlm.nih.gov/articles/PMC12102280/): bilateral L1/L3 ES/L5 multifidus,16channels,Trigno Avanti/2148Hz/10mm, rawCSV headerrow20; filtered _emg/normalized _envelope/MVC CSV; gaps/clipping. Back calibration: standing45°/manual-bracing/3×5s. Smallest archive **9,907,272,063bytes**, другие10.7–40GB; не скачаны, raw units/time/header не проверены. [Author reader revision f5c7591](https://github.com/robinsonmc/shearer-data/blob/f5c759109f70c03a613e7e2fc14bca71a983fb7d/read_delsys.py#L19) hardcodes2148.148148; discrepancy разрешать export timestamps. Author-code LICENSE не найден/licenseAPI404: только format reference, не copying.

## Generic, отдельно от lumbar endpoint

**[Hyser v2.0.0](https://physionet.org/content/hd-semg/2.0.0/)**: ODC-By-1.0, forearm/finger tasks, raw/preprocessed WFDB/MVC subset. Actual [header](https://physionet.org/files/hd-semg/2.0.0/pr_dataset/subject01_session1/dynamic_raw_sample1.hea):256channels/2048Hz/2048samples/format16/**V**/gain/baseline/ED-8-8 labels. [README](https://physionet.org/files/hd-semg/2.0.0/readme.txt): four8×8arrays. Expected .dat1,048,576bytes по header, body не скачан; full142.8GB исключён. Preserve headerV, без input-referredµV/MVC inference.

**[Walking semg v1.0.1](https://physionet.org/content/semg/1.0.1/)**: ODC-By-1.0, able-bodied legs/2000Hz/10EMG+4auxiliary, WFDB; electrode geometry/filters опубликованы, MVC не заявлен. Actual [S01.hea](https://physionet.org/files/semg/1.0.1/S01.hea) **mV**, provider text **µV**: unit_conflict gate, без implicit×1000. Body не проверен.

## Processing / ProviderProtocol

Proposed profile: csv.DictReader/gzip.open потоково; WFDB [rdheader/rdrecord API](https://wfdb.readthedocs.io/en/latest/io.html), ограниченный channel/sample-range. Manifest: DOI/license/hash/device/export, channel-order/muscle/side/placement/level, fs/timestamps/unit/gain/rails/analog-filters/task/MVC, subject/session/linkage/acquired_at/clock-domain; неизвестное null.

QC до correction: isfinite/time-monotonicity/duplicates/gaps/flatline; gaps split segments без fill. Unknown rails → clipping_unverifiable. fs missing/conflict → filter/spectral not_assessed; units missing/conflict → calibrated RMS not_assessed. Placement/MVC missing блокируют anatomical comparison/%MVC.

Explicit proposed filter: [butter](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html)(N=4,Wn=[20,450],btype=bandpass,fs=fs,output=sos), [sosfiltfilt](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html)(axis=0,explicit-padding). Проверить450<Nyquist/segment-length, no fallback; coefficients/effective-order/edge-exclusion сохранять. Notch только declared50/60Hz. Technical profile, не clinical standard.

RMS=sqrt(mean(x²)) на bandpassed signal, proposed250ms/100ms-hop, verified source-unit. Envelope отдельно. [Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html): explicit fs/window/nperseg/overlap/nfft/detrend/scaling, proposed1s/.5s-hop; MDF=half integratedPSD, MNF=integrated(f×PSD)/integratedPSD, zero-power gate. Спектр не на envelope; unknown amplitude-unit не блокирует пригодный frequencyHz, но не позволяет подписывать PSD как V²/Hz.

Existing `ProviderProtocol.analyze(request,context)->dict`; spec emg.surface0.1.0. `context.artifacts.open(ref)` для input, `add_file(...,role="derived")` для raw/processed/QC/PSD/plots/manifests; `result_envelope`→`validate_result(result,request)`. No arrays in value; no dispatcher/SQLite/clinical-engine imports.

Proposed evidence selector: type emg.sample_interval.v1, original 0-based [sample_start,sample_stop_exclusive), channel_index/label, fs, relative-time/clock-domain, processing-manifest reference. EMG-only index4=rawCSVcolumn6 Left ES в HipBridge; convention фиксировать. После resampling нужен mapping artifact. Proposed concepts rms/median_frequency/mean_frequency; origin automatically_measured/modelnull, synthetic_demo только fixture. Unknown acquisition time/spatial-frame null. QC gates partial/not_assessed, не normal/negative.

## Статус и границы

Проверены primary sources/licenses/exact revisions/small headers; packages/features не исполнялись. JSON parse/diff/ownership checks, без behavior tests. Next: single Hyser record вне Git с attribution/hash/QC, затем HipBridge unit clarification; Shearer small validated export отдельно. Synthetic sinusoid/gap/clipping/Nyquist/unknown-unit fixtures только предложены, не созданы.

Без training/hardware purchases/device SDK/streamers/capture. Recorded export не live capture. Surface features не диагностируют radiculopathy, не заменяют needle EMG/ЭНМГ, не оценивают правдивость жалоб. Healthy/generic/physiological-pain corpora не клинический dorsopathy corpus; unrelated MRI/симптомы не привязываются.
