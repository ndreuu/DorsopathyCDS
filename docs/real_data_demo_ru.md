# Демонстрация на настоящих публичных данных

Открыть <http://127.0.0.1:8766/real-examples>. Эта страница показывает реальные
входы и результаты рабочих providers. Кнопка под каждым примером открывает
все изображения, наблюдения, исходные/производные артефакты и клинический отчёт.
Значения в таблицах читаются из сохранённых snapshots, а не из текста каталога.

Каждый набор относится к отдельному человеку и загружается в отдельный episode
с `linkage=unlinked`. Клинические оценки отсутствуют: результат диагностического
правила остаётся `indeterminate`. Публичные изображения не дополнялись выдуманной
анкетой, врачебным заключением или нормальными показаниями датчиков.

## Как снова запустить

Весь код: `/Users/andrew/Projects/DorsopathyCDS`, ветка `feature/multimodal-mvp`.
Данные, преобразованные профили и локальные результаты: `var/real-inputs/`;
они игнорируются Git. `catalog.json` содержит ссылки на уже выполненные локальные
runs, `input-index.json` — выбранные файлы для повторного прогона.

```sh
cd /Users/andrew/Projects/DorsopathyCDS
PYTHONPATH=src python3 -m dorsopathy_platform serve \
  --store var/multimodal --port 8766 \
  --example-catalog var/real-inputs/catalog.json
```

Для этой страницы и replay МРТ веса не требуются. При необходимости fresh MRI
добавить `--mri-config var/mri-worker.json`; инструкция в `mri_provider_ru.md`.
Не запускать второй controller над тем же store. Если сервер уже работает,
просто открыть ссылку страницы. Каталог перечитывается при загрузке страницы.

Повторно загрузить выбранные входы в работающий сервер и создать новые snapshots:

```sh
PYTHONPATH=src python3 scripts/run_real_examples.py \
  --index var/real-inputs/input-index.json \
  --output var/real-inputs/catalog.json
```

Script принимает только loopback HTTP, потоково загружает выбранные файлы,
сверяет SHA256 ответов, сохраняет порядок input refs и ждёт фактические terminal
results. Он не меняет source JSON или модели. По умолчанию wait timeout 600 s;
после timeout запущенный run сохраняется. `--append` добавляет примеры с новыми
ID, сохраняя существующий каталог. Источники и preview IDs проверяются; чужой
artifact не становится картинкой другого пациента. Runtime index не публикуется
в Git вместе с настоящими входами.

## МРТ: анатомия канала на аксиальных срезах

[Первичный набор Sudirman et al., Lumbar Spine MRI Dataset v2](https://data.mendeley.com/datasets/k57fr854j2/2),
DOI 10.17632/k57fr854j2.2, CC BY 4.0. Использован уже скачанный анонимизированный
пример 0001: сагиттальная T2 и аксиальная T2. Внешний исходный export SpinoSarc:
`20261008-032437-23b70c/findings.json`.

Normalizer `dorsopathy_modules.mri_import` прочитал реальные DICOM и NIfTI-маску,
сверил размеры/геометрию источников и повторно вычислил площадь после переноса
маски на родные аксиальные плоскости неизменённым SpinoSarc helper. Прогон provider
работает как **replay прежней модельной сегментации**, а не fresh inference.

| Уровень, требует подтверждения | Площадь канала |
|---|---:|
| L3–L4 | 126.19921875 мм² |
| L4–L5 | 106.34765625 мм² |
| L5–S1 | 123.8359375 мм² |

L1–L2/L2–L3 не оценены из-за отсутствия axial coverage. Совмещение серий требует
проверки. Красная область — анатомическая маска канала, не выделенная грыжа и
не отдельная верифицированная маска дурального мешка. CSA не превращается
автоматически в степень стеноза. Все arrays, PNG и методы связаны evidence.
Bundle полностью хранится в `var/real-inputs/mri/sudirman-axial/`.

## УЗИ: физический масштаб настоящего поясничного кадра

[KU Leuven RDR, DOI 10.48804/3XPCAE](https://doi.org/10.48804/3XPCAE), CC BY 4.0;
[описание в Scientific Data](https://doi.org/10.1038/s41597-025-06047-9).
Выбран `URS35_H3/HUS/0.png`, реальный поясничный кадр здорового добровольца.
Crop 390×780 точно соответствует опубликованному `full[150:930,550:940]`,
без resize или изменения интенсивностей. [Калибровочный CSV](https://rdr.kuleuven.be/api/access/datafile/271414)
для H3 задаёт X=0.0949559 и Y=0.09310205 мм/пиксель;
[crop README и calibration](https://rdr.kuleuven.be/api/access/datafile/358659).

Ручная техническая линия даёт 7.35506195 мм, ROI — 53.69773728 мм². Это проверка
измерительного конвейера, не автоматическая сегментация мышцы или патологии.
Автор разметки сохранён; уровень/сторона неизвестны. Полный первый PNG получен
из частично скачанного ZIP с проверкой CRC32; MD5 всего ZIP не утверждается.
Original PNG, crop, metadata, source/result/provenance: `var/real-inputs/ultrasound/`.

## ЭМГ: настоящая поверхностная запись поясничных мышц

[HipBridgeEMG v0.1](https://zenodo.org/records/17465228),
DOI 10.5281/zenodo.17465228, CC BY 4.0. Два канала erector spinae longissimus
из `fd0f5636-phe-1.csv.gz`, BTS FREEEMG, 1000 Гц. MD5 оригинала совпал с Zenodo.
Первые 8192 отсчёта выбраны без ресемплинга; временная колонка проверена против
частоты. 16 окон по 1024 отсчёта, mean detrend, rectangular FFT, без фильтрации.

MDF первого окна: слева 66.40625 Гц, справа 61.5234375 Гц. Независимый прямой DFT
подтвердил оба результата. Все 16 частот оценены; RMS в физических единицах
не оценён, поскольку amplitude scale/gain не подтверждены. Clipping unknown,
пропусков и конфликтов времени не найдено. Абсолютное время неизвестно.
Никакой клинической классификации боли/радикулопатии не добавлялось.
Источник, профиль, CSV, independent checks и графики:
`var/real-inputs/emg/hipbridge-phe-v0.1/`.

## Термография: радиометрический кадр спины

[Авторский back-temperature-analysis](https://github.com/dshatwell23/back-temperature-analysis),
revision `729906f1a5949e316728e730e9820b75d3a36c76`, файл `images/FLIR2220.jpg`.
Исходный файл публичный, **явная лицензия не обнаружена**: он не включается
в распространяемый или обучающий датасет с утверждением открытой лицензии.
Демонстрация локальная; перед дальнейшим переиспользованием нужны права.

ExifTool извлёк raw 240×320 sensor counts и коэффициенты FLIR. Преобразование
использует file-specific Planck coefficients, emissivity, расстояние, ambient,
reflected temperature, влажность и window parameters, по
[реализации raw2temp Thermimage](https://github.com/gtatters/Thermimage/blob/master/R/raw2temp.R).
Это не восстановление температуры из цвета palette JPEG.

Две вручную выбранные центральные ROI: 33.326978886 и 32.400692255 °C.
Spot check: decoded 33.824 °C округляется до 33.8 °C, показанных камерой.
Численное декодирование проверено; фактические emissivity, blackbody accuracy
и условия процедуры независимо не подтверждены. Калибровка profile описывает
декодирование file metadata, а не метрологическую поверку. Сторона и уровень
не присваиваются; воспаление, клиническая норма и источник боли не определяются.
Все исходники/метаданные/конверсия и profile находятся в
`var/real-inputs/thermography/`.

## 3D: настоящая поверхность тела из depth-кадра

[TEXEL BodyScan](https://github.com/m-krivov/Texel-BodyScan-Dataset),
revision `5b3ba6fa1606e92205c3bdebde53b401a8c999a6`, Part1 Man0,
`frames/392528928.png`. Лицензия данных CC BY-NC 4.0; лицензия parser MIT отдельно.
Это реальный кадр ToF-камеры Azure Kinect, не запись LiDAR-стойки.
Исходный 16-bit depth960×864 преобразован в 6847 видимых поверхностных точек,
без подгонки SMPL или добавления синтетических кадров FreeFusion.

Единица DEPTH16 — мм по [Microsoft SDK](https://github.com/microsoft/Azure-Kinect-Sensor-SDK/blob/develop/include/k4a/k4atypes.h).
Projection использует supplied cx/cy/fx/fy, каждый третий пиксель и опубликованный
body bounding box. Авторы указывают distortion mitigation OpenCV; остаточная
погрешность и воспроизводимость физической геометрии независимо не проверены.
Источник, преобразование, original-pixel→vertex mapping и точная привязка
content-addressed PLY сохранены в `var/real-inputs/lidar/`.
Для повторного прогона используется `source-bound.json`, где placeholder
заменён SHA-ID действительного PLY; исходный `source.json` сохранён отдельно.

Ручные точки поверхности дают длины322.168169 и434.587089 мм,
трёхточечный угол38.267054°. Это технические поверхностные измерения,
не валидированные костные ориентиры, угол Cobb или диагноз. Один ракурс
покрывает часть поверхности; production result partial/qualityunreliable.

## ANI/NOL: почему пока не добавлен настоящий прогон

[Открытая DataFigure3 ANI-REMI-LOOP](https://plos.figshare.com/articles/dataset/DataFigure3_/28933933)
имеет CC BY 4.0. Excel скачан, MD5 проверен; данные содержат контрольные точки
T1–T7, а не подтверждённую временную сетку с quality. Времена не выдумывались.
[Multimodal Physiological Indices During Surgery Under Anesthesia](https://physionet.org/content/multimodal-surgery-anesthesia/1.0/)
содержит непрерывные записи ANI, но доступ ограничен и требует data use agreement.
Соглашение автоматически не принималось, файлы не скачивались.

`var/real-inputs/nociception/access-audit.json` фиксирует проверенные источники
и отсутствие пригодного входа. Ряд ECG/HRV не подставляется вместо ANI/NOL,
а perioperative indices не становятся подтверждением боли при дорсопатии.

## Проверка

Реальные значения вычислены текущими production providers через обычный
upload → job → snapshot → report. Это подтверждает работу импорта/измерений;
диагностическая точность по этим примерам не установлена. Manual annotations,
неизвестная шкала/покрытие и права на каждый источник показаны отдельно.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Набор из 288 tests завершился OK; один optional imaging test skipped в system Python.
Clinical rules и wire contracts 1.0 не менялись. Providers, upstream SpinoSarc,
родительский MRI backend и weights не изменялись для этого демо.
