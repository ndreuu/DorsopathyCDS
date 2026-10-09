# Канонический индекс прибора: импорт, окно и график

`NociceptionProvider`, module `nociception`, version `0.1.0`.
Initial source `nociception-index.v1` — **наш canonical JSON import profile**,
не проверенный vendor parser ANI/PMD-100/PMD-200 и не воспроизведение ANI/NOL.

## Source и алгоритм

[source.json](../tests/fixtures/nociception/source.json) — один source JSON artifact,
parameters пусты. Manufacturer/model/software/index_field/averaging_window_s,
state/protocol/time/clock, sampling_period_s, unit/calibration, quality/events
и выбранное окно сохраняются явно. Относительные timestamps time_s привязаны
к source acquired_at только при известном timezone; host clock не присваивается.
Unknown absolute source time допускает численный relative-window результат
с quality=unknown, но не пригодное временное сопоставление.

Rows `{time_s,value,quality,events}` используют canonical quality
usable/unreliable/unknown; это не автоматическое преобразование vendor codes.
Value finite 0…100 или null; unit=`1` или unknown/null. Selection `[start_s,end_s)`
включает ровно явно выбранные samples; начало окна должно совпадать с первой
выборкой в пределах явного period*tolerance, а хвост покрываться её cadence.
Пропуск первой выборки не допускается как допустимая фаза сетки.
Index `nociception.device_index`, unit1:
unweighted arithmetic mean выбранных device values без rounding, interpolation,
ресэмплинга или собственной HRV/ANI/NOL формулы.

Fixture device='Synthetic fixture/canonical-demo', index_field='demo_index',
rows20/40/60 at0/1/2 s, usable quality, selection[0,3) →40. При[0,2) →30.
Индекс уже получен устройством/fixture; вычисляется только выбранная агрегация.

Missing/unknown quality/value/time, gaps/incomplete window coverage, unknown
sample cadence, unit/calibration, index definition/averaging window исключают
requested scalar measurement. Они не становятся 0 и не означают отсутствия боли.
Duplicate/non-monotonic times, outside0…100 и undeclared events отклоняются.
Quality и gaps сохраняются в QC reasons; график не соединяет known cadence gaps.
Unknown index definition/averaging не заполняется временем другого устройства.

## Выход и проверка

Source series/events JSON, SVG/PNG график index-versus-time с events и
measurement/QC JSON — evidence. Exact selected row indices/window/device definition
сохраняются. SVG escaping защищает подписи событий от встроенного markup.
Comparison sidecar использует выбранный интервал, explicit pose/event/clock,
calibration reference; spatial frame/linkage/transforms не выдумываются.
Само clock_domain='UTC' не является измерением synchronization accuracy.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_nociception.py' -v
```

## Vendor/data/capture/clinical gates

[Research](../research/reuse/nociception/report_ru.md) проверил первичные manuals
MDoloris ANI V2 и Medasense PMD-200, отдельно PMD-100 research model.
PMD-100 не подменяется PMD-200. Предполагаемые имена CSV/TXT колонок не доказывают
точный delimiter/encoding/timezone/quality/export variant. Нет verified timestamped
vendor export — vendor adapter и real ANI/NOL import **не приняты**.

[Открытые Bollag PMD-100 data](https://doi.org/10.5256/f1000research.15279.d207164), CC0,
— perioperative window aggregates, не raw index/time/quality series; они не были
подставлены вместо требуемого vendor файла. Canonical importer проверен только
на synthetic fixtures; реальный source/export требует отдельного разрешённого
файла и сверки с прибором. Runtime uses stdlib, pandas/Plotly/serial не нужны
для initial profile и не устанавливались. Device capture/SDK/serial protocol
пока unsupported; declaration acquisition_mode='device' отклоняется.

Применимость к бодрствующим пациентам с дорсопатией — отдельный clinical gate.
Индекс не устанавливает наличие/отсутствие боли, диагноз, её причину, психогенность
или правдивость жалоб. Proprietary ANI/NOL из ECG/HRV не вычисляются.
