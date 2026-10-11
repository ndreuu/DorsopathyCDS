# Исходный depth → автоматическое облако и внешняя геометрия

`RawLidarProvider`, module `lidar.posture/0.2.0`, профиль
`lidar-depth.v2`, результат `ModuleResult 1.0`. Старый
`LidarPostureProvider/0.1.0` и его ручные vertex landmarks не изменены.

На входе **исходный DEPTH16 PNG и исходный файл камеры**, acquisition
manifest; готовый PLY и ручные landmarks новый профиль не принимает.
Предварительное получение контуров/измерений не требуется. Алгоритм автоматически
выделяет крупнейшую непрерывную по глубине компоненту, строит видимое облако
и рассчитывает его размеры. Компонента не объявляется подтверждённым
анатомическим детектором тела или позвоночника.

## Вход и запуск

[Синтетический acquisition manifest](../examples/lidar_raw/synthetic-acquisition.json),
[камера](../examples/lidar_raw/synthetic-camera.json),
[исходный depth](../examples/lidar_raw/synthetic-depth.png) —
проверяемая плоская поверхность 230×450 mm.

[TEXEL acquisition manifest](../examples/lidar_raw/texel-acquisition.json) —
шаблон для двух исходных файлов уже загруженного корпуса; `depth_artifact_id`
и `camera_artifact_id` заменяются после загрузки. Request содержит ровно
три **source** ArtifactRef: manifest JSON, PNG, calibration XML/JSON;
parameters пусты, protocol_id совпадает с manifest. Host paths в wire нет.

Поддержаны только неинтерлейсный grayscale PNG16 и axial-Z depth.
Все пять PNG row filters и big-endian uint16 PNG учитываются; in-memory
little-endian DEPTH16 нельзя переносить на прочитанные PNG samples.
Проверяются CRC/chunk order, header/dimensions, полный zlib stream,
ограничение распаковки, отсутствие лишних bytes.

`camera_format=texel.scan.xml.v1` автоматически читает width/height/fx/fy/cx/cy
из оригинального XML и не использует прежние подготовленные JSON/PLY.
В опубликованном XML объявлено UTF-16, но фактические bytes UTF-8:
парсер явно декодирует эти bytes, исходный artifact остаётся неизменённым.
DTD/entities отклоняются. Generic calibration JSON имеет профиль
`depth-camera-calibration.v1`, как в синтетическом примере.

```sh
PYTHONPATH=src python3 scripts/run_lidar_raw.py \
  --depth var/real-inputs/lidar/raw/392528928.png \
  --camera var/real-inputs/lidar/raw/person.scan.xml \
  --manifest examples/lidar_raw/texel-acquisition.json \
  --python-executable /usr/bin/python3 \
  --output var/lidar-raw-texel-v2
```

Core dependency-free. Для прямого запуска:

```python
from dorsopathy_modules.lidar_raw import RawLidarProvider
result = RawLidarProvider().analyze(request, context)
```

Trusted isolated вариант:

```python
provider = RawLidarProvider(
    python_executable="/absolute/path/to/python",
    workspace_root="/absolute/path/to/ignored/lidar-workers",
    timeout_seconds=120,
)
result = provider.analyze(request, context)
provider.close()
```

Worker совместим с ProcessProvider: trusted argv
`python -m dorsopathy_workers.lidar --exchange-dir <private-exchange>`.
Пакет должен быть установлен или src указан в абсолютном PYTHONPATH.
Python/command выбирает оператор, не загруженный manifest.

## Метод, units и QC

1. Foreground кандидаты — ненулевые samples в **явно заданном техническом**
   source-depth range. Четыре соседних пикселя соединяются, если различие глубин
   не больше max_neighbor_delta_units. Выбирается крупнейшая компонента;
   min_component_pixels и point_grid_stride объявлены явно. При равном размере
   сохраняется первая компонента в порядке исходных пикселей.
   Никакой ручной lateral ROI/bbox/vertex разметки или правки поверхности.
   Для TEXEL диапазон 1200…2700 взят из source capture volume по глубине;
   lateral limits прежнего bbox не используются.
2. Выбранные исходные пиксели проецируются без interpolation/hole filling/body fit.
   Z = depth × mm_per_unit при known scale; X = ray_x × Z, Y = ray_y × Z.
   `verified` scale требует source/author и положительный mm_per_unit:
   это подтверждённое заявление об единице, а не независимая поверка прибора.
   Unknown scale сохраняет облако в source_depth_units, но блокирует mm observations;
   scale не подбирается по предполагаемому росту. В comparison sidecar статус
   относится ко всей projection geometry: unknown при неизвестной distortion,
   invalid при непригодной/failed geometry, verified только при known scale
   и known camera correction. Source-unit evidence сохраняется с отдельным
   scope=depth_unit_scale_only и не удостоверяет полную геометрию.
3. Generic camera допускает документированную rectified pinhole
   (`distortion.model=none`) или Brown-Conrady k1,k2,p1,p2,k3.
   Brown inverse выполняется Newton с forward residual <1e-10;
   не сходящиеся/непригодные rays исключаются с QC.
   Без соответствующей distortion/rectification provenance геометрия
   остаётся `unreliable`, даже при опубликованных fx/fy и mm scale.
4. Population covariance и детерминированная симметричная 3×3 Jacobi
   eigendecomposition дают PCA axes по убыванию eigenvalue.
   Extent = max(projection) − min(projection). Это внешняя размерность
   **выбранной видимой компоненты**, не полный размер тела, мышц или позвонков.
   При близких eigenvalues базис PCA нестабилен; точность/повторяемость
   прибора и segmentation residual здесь не оцениваются.
5. `surface.visible_extent`, mm — три extent вдоль вычислительных
   camera_x_right/camera_y_down/camera_z_forward.
   `surface.principal_extent`, mm — три extent вдоль PCA axes.
   Instance IDs и evidence содержат конкретную axis; разные axes не смешиваются.
6. `posture.surface_axis_tilt`, deg — угол между первым геометрическим
   PCA axis и **реально известным** anatomical up vector, без направления:
   acos(abs(dot)). Нужны verified anatomical_frame/source/author,
   известный optical frame и отделённый первый eigenvalue
   (явный технический pca_axis_min_separation_ratio).
   Это не угол торса, таза или позвоночника. Sensor y-down сам по себе
   не доказывает anatomical up. Unknown orientation блокирует tilt;
   anatomy side asymmetry, landmarks, spinal curvature и ROM остаются null.

QC сохраняет missing/zero samples, количество кандидатов/компонент, выбранные
pixels, projection failures, image-border clipping, verified distortion,
calibration status, visible-only coverage. Ноль depth не заменяется нормой.
Компонента, касающаяся границы, получает unreliable geometry.
Нет автоматического подтверждения «это тело нужного пациента», detector
анатомических ориентиров, движения из одиночного кадра или скелетной регистрации.

Bounds: PNG до 32 MiB, до 2 Mi pixels, dimension до 4096, calibration/manifest
по 1 MiB, до 100000 cloud points. CRC/decompression/dimension mismatch/неизвестные
поля отклоняются; пустая компонента даёт partial с not_assessed, без диагноза.
Invalid metric calibration блокирует численные признаки. Распознавание
плоскости пола, одежды, multipath и полный body model этим методом не выполнены.

Artifacts: raw PNG/XML/manifest, cloud.ply с x/y/z + source_u/source_v/raw_depth,
CSV всех pixels выбранной компоненты, PNG overlay, method JSON и measurement/QC
JSON. Цветная компонента на PNG вычислена автоматически; серые пиксели — прочие
nonzero samples. Evidence каждого observation связывает исходный depth,
оригинальную calibration, компоненту, cloud/axis, method, QC и preview.
origin реального вычисления — automatically_measured; acquired_at/clock
остаются null, если достоверное время неизвестно. Нумерация позвонков не назначается.

## Реальный TEXEL прогон и границы

[TEXEL BodyScan Dataset](https://github.com/m-krivov/Texel-BodyScan-Dataset/tree/5b3ba6fa1606e92205c3bdebde53b401a8c999a6):
Part1/Man0, original frame `392528928.png` и `person.scan.xml`.
Исходный кадр получен Azure Kinect ToF, затем авторы применили OpenCV
distortion mitigation. Нужная точная post-rectification camera matrix/
coefficients/ray table в этом XML не дана: поэтому nominal cloud и все
измеренные extents **unreliable**, без утверждения о клинической точности.
Generic parser также поддерживает known calibrated Brown/rectified inputs.

Данные — **CC BY-NC 4.0**, attribution TEXEL LLC и Lomonosov Moscow State
University; изменения здесь: автоматический foreground, nominal camera
projection и геометрические признаки. Код авторского C++ parser имеет
отдельную MIT license, не меняющую noncommercial ограничение данных.
[Лицензия данных](https://github.com/m-krivov/Texel-BodyScan-Dataset/blob/5b3ba6fa1606e92205c3bdebde53b401a8c999a6/LICENSE-DATA).
Raw originals и outputs сохраняются вне Git.
Nominal DEPTH16 mm следует
[Microsoft Azure Kinect SDK](https://github.com/microsoft/Azure-Kinect-Sensor-SDK/blob/develop/include/k4a/k4atypes.h);
SDK unit не удостоверяет accuracy конкретного кадра/rectification.

Реальный независимый прогон: `var/lidar-raw-texel-v2/`.
960×864 PNG; 61690 nonzero pixels, крупнейшая компонента 59947 pixels,
6664 points после declared stride3. Семь observations: 6 nominal extents
оценены с unreliable, tilt not_assessed/unknown. Sensor extents:
786.056151/1746.643977/458 mm; PCA extents:
1767.017473/786.203972/283.754103 mm.
Точные величины и axes находятся в measurements.json.
Это не измеренные плечи/рост/ширина таза и не критерий дорсопатии.

Независимая проверка оригинального изображения через Pillow, компонент через
SciPy sparse graph и PCA через NumPy записывается в checks.json; provider
сам этих библиотек не требует. Эти проверки оценивают соответствие
вычислительному методу, не геометрическую точность сенсора или клиническую роль.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_lidar_raw.py' -v
```

Синтетика проверяет известную plane geometry, изменение исходной depth,
раздельные версии, subprocess wire, отсутствие scale/axes, unknown distortion,
Brown inverse, malformed input и пустую компоненту. Корпус не имеет клинической
разметки и не объединён с MRI/EMG/US/thermal участников. Нормальная внешняя
геометрия не исключает заболевание и не оценивает достоверность жалоб.
