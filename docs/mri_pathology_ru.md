# Автоматические оценки МРТ: SpineNet V2

Модуль запускает готовую нейросеть SpineNet V2 в отдельном процессе для двух
задач на каждом доступном поясничном дисковом уровне: центральный стеноз и
наличие грыжи. Это предсказания модели по изображению с сохранённым evidence;
они не подтверждают диагноз и не заполняют автоматически клинические правила
движка CDS. Модели не переобучались в этом проекте.

## Модель и поддержанный вход

Используется [официальный SpineNet](https://github.com/rwindsor1/SpineNet)
на commit `5e96ac54b1c1845a37d12e40bfd16f647d40acd8`.
Полный upstream-путь включает detector, appearance, context и grading.
Сначала SpineNet самостоятельно обнаруживает и нумерует позвонки, затем
извлекает свои межпозвонковые объёмы. Маски или ROI TotalSpineSeg не
подставляются вместо входа grading-модели.

Provider `mri.pathology` имеет версию `0.1.0` и режим `processing_mode=live`.
Первый вход worker — **один исходный 3D MHA или NIfTI volume** с регулярной
сагиттальной геометрией. T2 задаётся доверенной конфигурацией `--contrast T2`:
это утверждение о выбранной серии, а не автоматическое определение контраста
произвольного загруженного исследования. T1, STIR, несагиттальные серии и
несколько volumes не входят в этот профиль. DICOM-import для этого worker —
следующий этап, а не уже поддержанный сценарий.

API документация upstream описывает pixel spacing как пару, но
[реальный preprocessing](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/spinenet/utils/scan_preprocessing.py)
и контекстный этап используют скаляр. Поэтому профиль требует изотропный
шаг в сагиттальной плоскости; worker передаёт один row-spacing и сохраняет
**обе исходные калибровки** в геометрии evidence. Неизотропный шаг не
усредняется молча. Исходные направления, spacing и origin нужны для обратной
привязки к source-grid. Отсутствующая или непригодная геометрия означает
отказ оценки, а не отрицательную находку.

Grading обучен на sagittal T2 от T12-L1 до L5-S1. Неполное покрытие,
пропущенный позвонок, неоднозначная нумерация и неподдержанный вход должны
сохраняться явно. Неоценённый уровень не становится нормой; модельная
нумерация требует подтверждения.

## Две выходные головы и точное значение классов

В [GradingModel](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/spinenet/models/grading.py)
центральный стеноз — голова `fc_ccs`, выход index 2, четыре logits.
Грыжа — `fc_hrn`, index 10, два logits. Другие upstream-головы не означают
автоматического расширения заявленных задач этого модуля.

Оригинальный [технический отчёт, §4.3.2](https://arxiv.org/html/2205.01683#S4.SS3.SSS2)
определяет центральный стеноз следующим образом:

| Raw class index | Grade SpineNet | Значение исходной шкалы |
|---|---|---|
| 0 | 1 | no compression — компрессия не выявлена моделью |
| 1 | 2 | mild compression — лёгкая степень |
| 2 | 3 | moderate compression — умеренная степень |
| 3 | 4 | severe compression — тяжёлая степень |

Это шкала SpineNet/Genodisc. Авторы не называют её Schizas или Lee; она
не подменяется объединённым классом normal/mild из RSNA. Фраза исходной
шкалы «compression» не устанавливает компрессию конкретного корешка или
наличие клинического синдрома.

Грыжа — бинарный признак: raw class `0` означает отсутствие, `1` — наличие.
Числовое кодирование подтверждается
[ground-truth formatting](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/spinenet/utils/gt_formatting.py):
нулевые метки сохраняются, положительные преобразуются в единицу.
Определение в отчёте — выход nucleus pulposus через annulus fibrosus.
Этот выход не содержит размеры, сторону, протрузию/экструзию, миграцию,
секвестрацию или воздействие на конкретный корешок.

[Upstream format_gradings](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/spinenet/utils/gen_utils.py)
добавляет `+1` к центральному стенозу, Pfirrmann и narrowing, но не к
бинарной грыже. Raw indices и опубликованные grades сохраняются раздельно.
Обычный `grade_ivds` возвращает argmax, а не вероятность. Сырые logits
или softmax, если сохранены в техническом sidecar, не являются
калиброванной вероятностью диагноза.

## Evidence и связь с исходным изображением

Реальные предсказания имеют происхождение `model_predicted`. Для каждого
оценённого уровня нужны идентичность исходного ArtifactRef/SHA256, версия
кода, hashes весов, геометрия, статус нумерации, acquisition time либо null
и ссылки на производные изображения/JSON. Реальные MRI не связываются с
синтетической анкетой как с одним клиническим эпизодом без подтверждения.

Upstream crop для grading имеет форму `9 × 112 × 224`: девять сагиттальных
срезов, поворот/resize вокруг диска и нормализация по интенсивности соседних
позвонков. Worker сохраняет точный tensor в NPY и PNG-монтаж девяти срезов.
Монтаж показывает **что подано модели**. Отдельный source PNG показывает
исходный срез с полигонами соседних позвонков: это контекст локализации,
а не контур диска или грыжи. Если на показанном срезе нет собственного
полигонального detection, средний vertebral polygon обозначается отдельным
method в геометрии evidence.

Штатный `get_ivds_from_vert_dicts` возвращает volume и level_name, не affine
crop. Текущий sidecar сохраняет source-frame indices и vertebral context,
но явно отмечает `crop_transform_to_source` как неэкспортируемый upstream.
Поэтому exact crop нельзя объявлять обратно спроецированным контуром
патологии или использовать как её размер в mm. Изображения не объясняют
причинность предсказания. Пустые, nonfinite и constant crops, а также
требующие padding исходные срезы, не становятся отрицательными признаками.

## Подготовка весов

`scripts/fetch_spinenet.py` использует только стандартную библиотеку Python.
Он не устанавливает пакеты, не импортирует ML и не выполняет inference.
Checkout должен заранее иметь указанный HEAD:

```sh
git clone https://github.com/rwindsor1/SpineNet.git var/upstream/SpineNet
git -C var/upstream/SpineNet checkout 5e96ac54b1c1845a37d12e40bfd16f647d40acd8
python3 scripts/fetch_spinenet.py --upstream-root var/upstream/SpineNet
python3 scripts/fetch_spinenet.py --upstream-root var/upstream/SpineNet --verify-only
```

Загружаются ровно четыре checkpoint с официального Oxford HTTPS-сервера:
`grading/ckpt1.pt`, `appearance/ckpt187.pt`, `detect-vfr/ckpt435.pt`,
`context/ckpt16.pt`. URL сверены с
[официальным download_weights](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/spinenet/__init__.py).
Каждая загрузка потоковая, ограничена 1 GiB, имеет timeout и retries.
HTTP errors, HTML вместо checkpoint, неожиданное перенаправление и
несоответствие Content-Length отклоняются. Незавершённый `.part` не
заменяет готовый файл; успешная замена атомарная.

Manifest записывается в `spinenet/weights/manifest.json`:
`schema_version: spinenet-assets.v1`, `upstream_commit`, `license_url` и
`files` с `url`, `size_bytes`, `sha256` для каждого относительного пути.
Повторный запуск пропускает только файл, повторно проверенный по manifest.
Наличие первого checkpoint не останавливает проверку остальных.
В каждом model-directory разрешён ровно один ожидаемый `.pt`: upstream
выбирает checkpoint по filesystem creation time, поэтому лишний файл мог
бы незаметно изменить использованную модель.

Локально вычисленный SHA256 фиксирует скачанные bytes. Он не является
опубликованной авторами контрольной суммой или самостоятельной проверкой
клинического качества. После появления manifest изменившиеся на сервере
bytes не принимаются как та же версия. Код, окружение, MRI, веса и результаты
остаются в локальном ignored runtime и не коммитятся вместе с данными.

## Отдельное окружение модели

[requirements-spinenet.txt](../requirements-spinenet.txt) — optional список
закреплённых основных зависимостей worker. Использовать **Python 3.12**.
NumPy закреплён на `1.26.4` и должен оставаться `<2`: pinned upstream
использует `ndarray.ptp()`, удалённый в NumPy 2. Torch `2.5.1` соответствует
Torchvision `0.20.1`; OpenCV headless `4.10.0.84` и Shapely `2.0.7` выделены
в этот runtime, а не в зависимости платформы.

Версии pandas, SciPy, scikit-image, Pillow, SimpleITK, requests, tqdm,
pydicom, Matplotlib и typing-extensions сверены с metadata существующего
read-only `.venv-spinosarc` на Python `3.12.14`. Pydicom нужен для upstream
imports, хотя этот worker пока не принимает DICOM. Jupyter и GUI OpenCV
для inference не требуются. Этот файл не меняет `pyproject.toml` платформы.

Самостоятельная установка из корня DorsopathyCDS:

```sh
python3.12 -m venv var/envs/spinenet
var/envs/spinenet/bin/python -m pip install -r requirements-spinenet.txt
var/envs/spinenet/bin/python -m pip check
```

`var/` уже игнорируется Git: крупные ML-пакеты и окружение остаются там.
При этом платформа запускается своим Python. `MRI_PYTHON` в trusted config
указывает на `var/envs/spinenet/bin/python`; ProcessProvider не выполняет
`pip install` и не скачивает отсутствующие веса автоматически.

Текущий локальный вариант переиспользует уже существующий imaging Python
и отдельный ignored overlay `var/spinenet-deps` с OpenCV headless/Shapely.
Его `PYTHONPATH` включает overlay явно. При самостоятельном venv весь
`requirements-spinenet.txt` установлен в venv, и overlay можно убрать из
команды, чтобы соседние local packages не подменяли закреплённые версии.

## Совместимость pinned upstream в worker

Checkout SpineNet на указанном commit остаётся неизменённым. Worker применяет
два локальных приёма совместимости в отдельном процессе модели:

- Обычный `detect_vb` удаляет внутренние vertebral volumes. Для проверки QC
  worker повторно вызывает штатный `extract_volumes` на поверхностных копиях
  словарей позвонков. Эти volumes используются только для QC; исходные
  detections и штатный путь извлечения IVD для grading сохраняются.
- Upstream `poly2mask` обращается к удалённому alias `np.bool`. На время
  штатного `get_ivds_from_vert_dicts` worker связывает отсутствующий `np.bool`
  с `np.bool_`, а в `finally` восстанавливает исходное состояние NumPy.
  NumPy `1.26.4` также не содержит этот alias: одного pin `<2` недостаточно.

Алгоритм извлечения и исходные файлы upstream не меняются. Alias действует
только вокруг вызова crop в isolated worker и не попадает в Python платформы.
Факт его применения записывается в `provenance.compatibility_shims` sidecar.

## Отдельный процесс

Trusted argv для ProcessProvider строится владельцем платформы:

```sh
PYTHONPATH="$CDS/src:$CDS/var/spinenet-deps:$CDS/var/upstream/SpineNet" "$MRI_PYTHON" \
  -m dorsopathy_workers.mri_pathology --exchange-dir EXCHANGE \
  --upstream-root "$CDS/var/upstream/SpineNet" --device cpu --contrast T2 \
  --acquisition recorded
```

`CDS` — абсолютный путь DorsopathyCDS; `MRI_PYTHON` — Python отдельного
совместимого imaging-окружения. ProcessProvider создаёт `EXCHANGE`, frozen
request и mapping входных артефактов. Это не публичный HTTP-параметр host path.
`--acquisition recorded|synthetic` — optional доверенная настройка worker,
по умолчанию `recorded`. Она фиксирует тип acquisition в provenance и не
подменяет время получения данных: неизвестное `acquired_at` остаётся null.
Нельзя объявлять синтетический volume реальным исследованием. Для текущего
recorded CLI выбирается один уже проверенный исходный volume.

Из-за отдельного процесса платформа и clinical engine не импортируют Torch,
OpenCV или библиотеки пиксельных данных. Успех inference и его время
фиксируются только по реально завершённому запуску; документация не
приписывает текущему runtime цифры из статьи.

Для запуска через persisted platform flow подготовить trusted JSON config
с ровно `command` (argv array с абсолютными путями) и `timeout_seconds`.
`command` запускает worker Python через `/usr/bin/env` с явно заданным
`PYTHONPATH`; `--exchange-dir` в этот config не включается — его добавляет
ProcessProvider. Локальный пример хранится в ignored
`var/mri-pathology-worker.json` и содержит путь к выбранному imaging Python,
overlay и pinned checkout.

Из корня DorsopathyCDS, выбрав один исходный sagittal T2 MHA/NIfTI:

```sh
PYTHONPATH=src python3 scripts/run_mri_pathology.py \
  --input /absolute/path/to/selected-volume.mha \
  --config var/mri-pathology-worker.json \
  --store var/mri-pathology-demo \
  --output var/mri-pathology-output
```

Runner загружает bytes в ArtifactStore, создаёт отдельный episode и
`mri.pathology` live job, ждёт ProcessProvider и сохраняет `run.json`,
`result.json`, `report.json`. Он не придумывает жалобы или осмотр;
связь с клиническими данными отмечается `unlinked`. Крупные исходные и
производные изображения остаются в ArtifactStore. Exact результаты и
время конкретного inference добавляются только после его завершения.

## Завершённый запуск на публичной МРТ

10 октября 2026 завершён свежий CPU inference полного SpineNet-пути:
detector → appearance → context → штатные IVD crops → grading. Сохранённые
предсказания не переиспользовались. Вход — публичная sagittal T2
`series0001` из [Sudirman et al., Lumbar Spine MRI Dataset v2](https://data.mendeley.com/datasets/k57fr854j2/2),
DOI `10.17632/k57fr854j2.2`, лицензия исходной МРТ — CC BY 4.0.

Идентичность этого запуска:

- `run_id`: `run-b0850ab01e74496eb88a4a6dedc908aa`.
- `episode_id`: `episode-2bda927f6ff2443c86d053b9e2e7b43f`.
- SHA256 исходного volume:
  `92323a6fe0bc8e71bb65a657f778d04d07f8ae425dd2573e136795d9b921f723`.
- Все четыре официальных checkpoint скачаны и проверены по локальному
  manifest; суммарный размер — `821857187` bytes. SHA256 grading checkpoint:
  `b7259f0a32d438d30e58db0f36db0b401ae5e36cb58d3eddb650cd7a141778fb`.

Обе задачи имеют `assessment_status=assessed` на всех пяти уровнях:

| Уровень | Центральный стеноз: модельная категория | Грыжа: модельный бинарный признак |
|---|---|---|
| L1–L2 | `none` (raw 0, grade 1) | `false` |
| L2–L3 | `none` (raw 0, grade 1) | `false` |
| L3–L4 | `none` (raw 0, grade 1) | `false` |
| L4–L5 | `none` (raw 0, grade 1) | `true` |
| L5–S1 | `none` (raw 0, grade 1) | `false` |

Это наблюдения `model_predicted`, не подтверждённые врачом находки. Нумерация
всех уровней имеет `needs_confirmation`. `run.json` и `result.json` сохраняют
`status=partial`: выполнены две поддержанные задачи, остальные патологические
признаки не оценены. Этот статус не означает незавершённый inference;
`result.error` равен null.

Для каждого уровня сохранены source PNG с vertebral context, PNG-монтаж
девяти штатных IVD-срезов и точный NPY входа grading. Контекстные полигоны
относятся к соседним позвонкам, а не к грыже. Размеры грыжи в mm, морфология,
сторона, воздействие на корешки и состояние конуса не определяются этим
запуском. Исходное `acquired_at` неизвестно и остаётся null.

`provenance.duration_seconds` равен `10.838986624992685` s (около 10.84 s)
для CPU model pipeline. В этот замер не входят загрузка в ArtifactStore,
проверка assets и импорты окружения; это не полная задержка от загрузки файла
до отчёта и не гарантированное время для других исследований.

Выходы находятся в ignored `var/mri-pathology-output/run.json`, `result.json`
и `report.json`; исходные и производные артефакты — в
`var/mri-pathology-demo`. Каталог примера —
`var/mri-pathology-output/catalog.json`. Жалобы и осмотр для этой МРТ не
созданы, linkage остаётся `unlinked`, а клиническое правило —
`indeterminate`: необходимы реальные клинические оценки и подтверждение
соответствия именно этого исследования клинике. Правило не менялось.

Для просмотра сохранённого результата из корня DorsopathyCDS:

```sh
PYTHONPATH=src python3 -m dorsopathy_platform serve \
  --store var/mri-pathology-demo --port 8767 \
  --mri-pathology-config var/mri-pathology-worker.json \
  --example-catalog var/mri-pathology-output/catalog.json
```

[Обзор реального примера](http://127.0.0.1:8767/real-examples) и
[полный результат этого запуска](http://127.0.0.1:8767/#run=run-b0850ab01e74496eb88a4a6dedc908aa)
доступны при работающем локальном сервере. Для перезапуска сначала остановите
предыдущий сервер. Не запускайте CLI runner с `--store var/mri-pathology-demo`,
пока сервер держит этот store: остановите сервер или выберите новый store
для нового inference.

## Условия использования и предел проверки

[Лицензия SpineNet](https://github.com/rwindsor1/SpineNet/blob/5e96ac54b1c1845a37d12e40bfd16f647d40acd8/LICENCE.md)
распространяется на код, опубликованные веса **и результаты**. Она допускает
строго некоммерческие исследования; код, производные результаты и
псевдоразметку нельзя автоматически считать разрешёнными для коммерческого
продукта. Для другого применения требуется отдельная лицензия авторов.
Авторы прямо указывают research-only: это не diagnostics tool или medical
device.

В оригинальном отчёте balanced accuracy на withheld Genodisc составляет
64.9% для четырёх классов центрального стеноза и 80.4% для грыжи. Это
результат авторов на их корпусе, не клиническая валидация DorsopathyCDS.
Прохождение software tests, integrity checks и одного публичного примера
подтверждает соответствующие технические операции, но не переносит
медицинскую точность на новый набор или допуск к применению.
