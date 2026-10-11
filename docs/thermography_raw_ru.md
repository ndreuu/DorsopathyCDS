# Радиометрический FLIR JPEG → автоматическая термальная область

`RawThermographyProvider`, `thermography/0.2.0`, получает исходный радиометрический
JPEG и acquisition manifest. Он сам извлекает counts, применяет коэффициенты
конкретного файла, выделяет область и сохраняет температурную матрицу, маску,
heatmap и QC. Старый `ThermographyProvider` `0.1.0` с готовой матрицей/ручными ROI
сохраняет свой API и поведение. Worker — отдельный доверенный процесс.

## Поддерживаемый вход и конфигурация

Поддерживается FLIR radiometric JPEG со встроенным **16-bit grayscale PNG**:
неинтерлейсный, до 1024×1024 пикселей, файл до 32 MiB. TIFF, SEQ, CSQ, ATS, DJI,
видео и обычный palette JPEG не заявлены как реализованные форматы. Сам факт
JPEG, наличие цветов или видимой температурной шкалы не означает радиометрию.
Отсутствие нужного raw payload/coefficients/settings даёт failed, без observations.

В `ModuleRequest 1.0`: `module_id=thermography`, `module_version=0.2.0`, ровно два
source ArtifactRef — `image/jpeg` и `application/json`; `parameters={}`. JPEG
привязан через `jpeg_artifact_id`, protocol manifest совпадает с request. JSON
manifest ограничен 64 KiB и требует точного набора полей из
[шаблона](../examples/thermography_raw/acquisition.template.json). Ручных ROI,
масок, точек, сторон и команд выполнения в этом профиле нет.

`count_byte_order` — известный порядок uint16 именно **sensor PNG**, `little`
или `big`. [ExifTool FLIR parser](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/FLIR.pm)
поясняет, что встроенный PNG извлекается как есть и многие FLIR камеры записывают
его с обратным порядком bytes. Непроверенный порядок нельзя угадывать по красивому
изображению. В проверенном E4 это `little`; source raw counts сохраняются отдельно
как row-major uint16 LE и с явным descriptor.

`conditions` хранит emissivity, distance m, reflected/ambient/window Cel,
window transmission и **relative humidity fraction [0,1]**. Null означает
использование записанного соответствующего FLIR tag, с сохранением tag provenance;
если и tag неизвестен, преобразование не выполняется. Явная known настройка из
manifest требует `source`; каждое override сохраняется отдельно от исходных tags.
`file_settings_only` не означает независимую проверку фактических условий.
`independently_verified` требует всех explicit settings и source; `unknown`
сохраняет неизвестность физической точности. Не используются коэффициенты другой
камеры или встроенные defaults Thermimage. Factory file coefficients и независимая
метрологическая калибровка — разные сведения.

Время — source timezone-aware ISO8601 либо null; системное время запуска его не
заменяет. Metadata сохраняет file timestamp отдельно. Pose, session/protocol,
coordinate frame и неизвестные значения не выдумываются. Source license остаётся
`unknown`, `permitted` с reference либо `synthetic`; provenance не выдаёт публичный
доступ за разрешение на распространение.

```python
from pathlib import Path
from dorsopathy_modules.thermography_raw import RawThermographyProvider
from dorsopathy_platform.process_provider import ProcessProvider

provider = ProcessProvider(
    RawThermographyProvider.spec,
    ("/trusted/python", "-m", "dorsopathy_workers.thermography",
     "--exiftool", "/trusted/exiftool", "--perl", "/usr/bin/perl"),
    Path("var/thermography-workers"), timeout_seconds=120,
)
```

`--perl` нужен для upstream source script; для установленного executable его
можно опустить. Python worker stdlib-only; зависимости платформы не меняются.
ExifTool argv задан только доверенным локальным оператором. Request не выбирает
исполняемый файл или shell. `-config ""` отключает загрузку пользовательского
ExifTool config. Требуется абсолютный PYTHONPATH к `src`, поскольку worker меняет
cwd на private exchange. Aggregate config example —
[worker-config.example.json](../examples/thermography_raw/worker-config.example.json).
Запуск платформы с `--raw-workers-config` относится к общей bootstrap-интеграции.
Workers доверенные; отдельный процесс не является security sandbox для native tools.

## Метод области и температурные признаки

Метод `gross_thermal_foreground_otsu256_largest4connected_v1` строит 256-bin
histogram по valid Cel pixels, выбирает Otsu threshold и крупнейшую четырёхсвязную
область выше threshold bin. При равенстве выбирается минимальный threshold,
затем первый component в row-major order. Размер менее max(16, 1% frame) или более
95% frame делает ROI непригодным; постоянный кадр также не даёт ROI. Fraction,
pixel count, bounding box и touching-border count сохраняются в QC. Область
определяется заново из текущих pixels, без ручной разметки или cached source.json.

Это **грубый тёплый foreground**, пригодный как начальный исследовательский признак.
Он может включать одежду, фон или другой тёплый предмет, пропускать холодную кожу;
его нельзя назвать валидированным detector тела/кожи или поясничных мышц.
Анатомическая область — `gross_thermal_foreground`, сторона `unspecified`,
level=null. Отдельной совместимой RGB/pose модели с проверенным thermal alignment
в этом worker нет. `model=null`, geometric method/version и отсутствие validation
явно сохранены. Для автоматических кожных/анатомических ROI нужны соответствующая
модель и проверка на подходящем корпусе; этот этап ещё не реализован.

`thermal.roi_mean_temperature` и `thermal.roi_max_temperature`, **Cel**, вычисляются
по точно сохранённой mask; min есть в measurements. Origin recorded —
`automatically_measured`; synthetic tests — только `synthetic_demo`.
`thermal.left_right_delta`, **K**, остаётся `not_assessed`/null: threshold mask не
подтверждает сопоставимые анатомические стороны и ориентацию. Поэтому результат
`partial`, даже когда радиометрия и ROI arithmetic выполнены. Геометрическое
разделение изображения пополам не используется для имитации bilateral measurement.

Невалидные/saturated counts, неверные log/Planck domains и неfinite values не
превращаются в нули. Invalid pixels в Celsius matrix — null; QC сообщает их число.
Отсутствие всех valid pixels даёт failed. Invalid calibration исключает assessed
температурные observations. Отдельной валидации foreground нет, поэтому quality
никогда не становится `usable`: `unknown` при пригодной арифметике, `unreliable`
при invalid calibration или invalid pixels. Эти проверки проверяют численный
pipeline; они не доказывают метрологическую или клиническую точность.

## Evidence и лицензии инструментов

ModuleResult wire schema остаётся `1.0`. Исходный JPEG и acquisition manifest
объявлены как source; производные counts, extracted sensor PNG, очищенные FLIR
tags, conversion conditions/provenance, temperature matrix, binary mask, native
heatmap, SVG с numeric Cel scale и measurements/QC связаны evidence. Оси матрицы
`row_y,column_x`, top-left, x-right/y-down; anatomical side из image-left не выводится.
Heatmap имеет белый mask contour и чёрные invalid pixels; PNG mask — white=1,
black=0. SVG caption объясняет отсутствие анатомической валидации.

[Официальный ExifTool](https://github.com/exiftool/exiftool) извлекает JPEG/FFF
metadata и sensor PNG; собственный FLIR binary parser не добавлен. Его
[README](https://github.com/exiftool/exiftool/blob/master/README) содержит dual
Perl Artistic OR GPL license и зависимость Perl. Численный converter переиспользует
существовавший локальный `var/real-inputs/thermography/prepare_thermal.py` и equations
[Thermimage raw2temp](https://github.com/gtatters/Thermimage/blob/master/R/raw2temp.R).
В [DESCRIPTION](https://github.com/gtatters/Thermimage/blob/master/DESCRIPTION)
заявлено GPL>=2, [LICENSE.md](https://github.com/gtatters/Thermimage/blob/master/LICENSE.md)
содержит GPLv3. Адаптированный `radiometry.py` явно имеет GPL-3.0-or-later notice;
его нельзя распространять как компонент без GPL obligations. Reference hash и
источник сохранены в каждом результате. Upstream R code/default camera calibration
не загружаются; R runtime в данном минимальном worker не требуется.

## Реальный локальный запуск

Публичный [back-temperature-analysis](https://github.com/dshatwell23/back-temperature-analysis),
revision `729906f1a5949e316728e730e9820b75d3a36c76`, `FLIR2220.jpg`, SHA256
`5a7328161739b178e32288d9ebcb8e239a2d86478749839effcf15d02df1d015` уже находится в
игнорируемом `var/real-inputs/thermography/raw/`. Явная лицензия неизвестна; real
bytes и derived artifacts не добавляются в Git. Это local research study,
без clinical ground truth и без связи с людьми в других публичных примерах.

```sh
PYTHONPATH=src python3 scripts/run_thermography_raw.py \
  --jpeg var/real-inputs/thermography/raw/FLIR2220.jpg \
  --manifest examples/thermography_raw/public-back-acquisition.json \
  --output var/real-inputs/thermography/automatic-raw \
  --exiftool /absolute/trusted/exiftool --perl /usr/bin/perl
```

Script выполняет настоящий ProcessProvider, самостоятельно извлекает raw PNG из
исходного JPEG, сохраняет bound manifest, request/result, matrix, mask/heatmap и
run metrics. Template token `bind-on-import` заменяется только runner; при обычной
загрузке нужен реальный JPEG ArtifactRef ID. Camera spot [133,85] можно независимо
сопоставить с видимыми 33.8 Cel; это численная проверка декодирования, не оценка
диагностической точности. Результаты актуального automatic run и их QC лежат в
`automatic-raw/`, а не в ранее рассчитанном decoded/manual-ROI profile.

Проверенный запуск исходного E4 от 10 октября 2026: 240×320; mask 40 280 pixels
(52.4479% frame), threshold 28.6956297457 Cel, ROI mean **32.129695917 Cel**, min
28.739319797 Cel, max **35.431545324 Cel**. Invalid/saturated pixels — 0. Spot
[133,85] = **33.8241093405 Cel**, совпадает с camera 33.8 после округления.
Gross mask включает тёплый торс/руки и часть одежды; это видимая граница метода,
а не подтверждение кожной/поясничной сегментации. Quality unknown; delta null.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_thermography_raw*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Температура и её асимметрия не устанавливают воспаление, источник боли,
радикулопатию, клиническую норму или отсутствие заболевания. Новых клинических
правил/порогов этот provider не добавляет.
