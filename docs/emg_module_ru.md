# Поверхностная ЭМГ: timestamped CSV, QC и оконные признаки

`EMGSurfaceProvider`, module `emg.surface`, version `0.1.0`.
Initial profile `emg-csv.v1` — CSV с явной колонкой относительного `time_s`
и metadata JSON, без автоматического распознавания vendor-формата или capture.

## Вход

[source.json](../tests/fixtures/emg_surface/source.json) +
[signal.csv](../tests/fixtures/emg_surface/signal.csv) синтетические.
Загрузить CSV как `text/csv`, заменить `csv_artifact_id`, затем загрузить manifest.
Request содержит два source ArtifactRefs, parameters пусты. Колонки time/channel
точно задаются manifest и не выводятся из изображения сигнала.

Manifest сохраняет sample_rate_hz (или null), каналы/сторону/мышцу, placement
(или null), общий uV_per_unit calibration для объявленных channels, ADC rails
в исходных units (или null), pose/protocol/source time/clock и processing.
Observation сохраняет исходную body region, например lower_back, и сторону
канала. Название мышцы остаётся в channel evidence, не заменяет регион тела.
Каналы с разным gain требуют предварительно нормализованного экспорта/нового
профиля; один средний gain не применяется к разным неизвестным каналам.
Known calibration требует source/author_id, но software не поверяет прибор.
Физические uV не выводятся из амплитуды записи или имени датасета.

Bounds: JSON/CSV по 8 MiB, до 32768 samples/8 channels, FFT window power-of-two
16…2048 samples, hop 1…window, до 2048 channel-windows и 524288 processed values.
Duplicate headers/non-monotonic timestamps/Inf и неподдержанные parameters
отклоняются. Empty/NA/NaN CSV cells сохраняются как missing, не forward-fill.

## Метод

Processing задаётся явно: filter=`none`, detrend=`none|mean`, spectral_window=
`rectangular`, window_samples/hop_samples и timing_tolerance_fraction.
Никакого default bandpass/notch/MVC/resampling. Mean detrend вычитает среднее
**каждого окна** до обоих вычислений; при overlapping windows сохраняются
отдельные window-scoped processed samples.

- `emg.rms`, uV: `sqrt(mean(processed_x²))`, только known amplitude scale.
- `emg.median_frequency`, Hz: one-sided squared FFT magnitude, positive-frequency
  bins удвоены кроме DC/Nyquist; первый bin, где cumulative power достигает 50%.
  Это discrete-bin rectangular periodogram MDF, не Welch PSD и не continuous
  interpolation. Frequency resolution=`fs/N`; raw power имеет uV2 или явно
  source_units_squared, не называется PSD/uV2-per-Hz.

Fixture `10*sin(2π*125*n/1000)`, N=128: RMS=`sqrt(50)`≈7.0710678119 uV,
MDF=125 Hz с bin16. После offset+20 RMS=`sqrt(450)`, MDF=0 Hz (DC);
explicit mean detrend возвращает исходные значения. Zero total power даёт
undefined MDF/null, а RMS=0 остаётся реальным числом с unreliable quality.

Missing samples/timestamps, timing gap/rate conflict, clipping и incomplete
fixed window исключают его metrics; соседний пригодный канал остаётся независимым.
Допуск временной сетки — технический явно заданный профиль, не медицинский порог.
Unknown fs исключает MDF; RMS при известных amplitudes сохраняется quality=unknown.
Unknown amplitude scale исключает uV RMS, но валидная частота может сохраняться.
Unknown ADC rails отмечают clipping_unverifiable/quality=unknown, не «сигнал нормален».

## Evidence и проверка

Исходные CSV/manifest, window-scoped processed CSV, raw graph SVG/PNG,
measurement/QC/spectrum JSON остаются artifacts. Evidence содержит channel_id,
original 0-based sample_start/stop_exclusive, window и method; arrays не помещаются
в Observation.value. Graph показывает raw source amplitude versus sample index,
не выдаётся за rectified envelope или calibrated uV, если source scale неизвестна.
Нет сглаживания/interpolation across missing samples.

Comparison sidecar содержит фактический расчётный acquisition interval,
explicit clock domain, protocol/pose/calibration evidence; spatial frame null.
Clock accuracy/synchronization evidence неизвестны, сам ISO/UTC не доказывает
синхронизацию разных приборов. MVC/reference не выдумывается (`null`).

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_emg_surface.py' -v
```

## Data/device gates

Generic canonical CSV import и численные методы реализованы на stdlib; NumPy,
SciPy/WFDB/LibEMG не устанавливались. Reference FFT ограничен указанными bounds;
для расширения алгоритма использовать versioned NumPy/SciPy worker, сохранив
метод/QC и не меняя его молча. Все committed fixtures synthetic_demo.

[HipBridgeEMG](https://zenodo.org/records/17465228), CC BY 4.0, — подходящий recorded
back-muscle кандидат, но source units/gain/ADC rails/MVC не подтверждены.
Выбраны и импортированы реальные 8192 отсчёта bilateral back-muscle trial;
MDF проверена независимым DFT, RMS остаётся not_assessed. Полный экспорт
и calibrated uV остаются отдельными gates. [Реальный пример](real_data_demo_ru.md).
Hyser — forearm WFDB, не поясничный корпус; Shearer archives большие и не скачаны.
Лицензии/проверенные headers — [research report](../research/reuse/emg/report_ru.md).
Generic CSV profile не объявляется tested FREEEMG/Delsys/WFDB driver.

Нет surface EMG radiculopathy classifier, needle EMG/ЭНМГ, pain-cause/MVC/side
diagnostic interpretation или оценки правдивости жалоб. Capture требует отдельного SDK.
