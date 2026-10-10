# LiDAR: внешний mesh и ручные ориентиры

`LidarPostureProvider`, module `lidar.posture`, version `0.1.0`.
Рабочий профиль импорта `lidar-manual.v1`: ASCII PLY 1.0 или OBJ,
JSON калибровки/позы/сессии и ручная разметка внешних ориентиров по vertex index.
Provider не реконструирует облако из фотографии и не подключается к сканеру.

## Вход и пример

Готовые полные источники: [source.json](../tests/fixtures/lidar/source.json),
[triangle.ply](../tests/fixtures/lidar/triangle.ply),
[triangle.obj](../tests/fixtures/lidar/triangle.obj). Все fixtures синтетические.
Сначала загрузить mesh, затем заменить `geometry_artifact_id` его ArtifactRef ID
в source JSON и загрузить JSON. Request содержит эти два исходных артефакта,
`parameters={}`. Host path/filename в запросе не используется.

PLY поддерживает scalar vertex properties с x/y/z и faces с index list;
другие element layouts, binary PLY и больше 100000 vertices/faces отклоняются.
OBJ поддерживает v/f, positive/negative face indices; внешние материалы не
загружаются. Индексы исходных vertices сохраняются. Coordinate/frame/axes,
source time, pose и coverage являются отдельными полями source JSON.

`calibration.mm_per_unit` — положительный масштаб, `status=verified` требует
явных source/author_id. Это проверка записанного заявления о масштабе, а не
метрологическая поверка прибора. Unknown scale остаётся неизвестным.
`annotations.author` обязателен; каждый landmark выбирает существующий vertex.
Ненайденный landmark в measurement даёт `not_assessed`, остальные расчёты сохраняются.

## Метод и выход

- `posture.landmark_distance`, mm: `mm_per_unit * norm(B-A)`.
- `posture.landmark_angle`, deg: угол ABC по двум ненулевым векторам AB/CB.
  Трёхточечный угол не требует масштаба и не называется углом Cobb.

Fixture A=(0,0,0), B=(300,0,0), C=(300,400,0), scale=1:
AC=500 mm и ABC=90 deg. При perturbation C.y=401 расстояние становится
`sqrt(300²+401²)`; результат не является заранее записанным числом.
Unknown scale исключает расстояние в mm, но сохраняет валидный угол.
Partial/unknown coverage сохраняет explicit partial/quality.

Artifacts: исходные mesh/JSON, отдельные authored annotations JSON,
normalized geometry JSON/ASCII PLY (mm при известном масштабе; иначе source_units),
обзор XY как SVG/PNG и measurement JSON. PLY открывается в Open3D/CloudCompare
внешним приложением; встроенный 3D-viewer не реализуется этим provider.
Обзор — проекция XY, не анатомическая регистрация. Source vertex indices и
measurement points_source_units позволяют проверить линию независимо.
Повторяемость/погрешность неизвестны (`null`), численные координаты не заменяют
измерение ошибки сканера или ручной разметки.

`origin=synthetic_demo` при synthetic acquisition; recorded использует
automatically_measured для вычисления по ручным ориентирам. Авторство разметки
остаётся в evidence. Comparison sidecar сохраняет session/subject/protocol,
time/clock/pose/frame, calibration evidence; linkage/transform refs пусты.
Совпадение имён frames не доказывает совпадение с MRI/DICOM frame.

## Приёмка и реальные данные

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_lidar_posture.py' -v
```

Численные и format/geometry tests выполнены на synthetic fixtures. Generic
ASCII recorded import реализован; настоящий ToF body depth-кадр TEXEL прошёл import после отдельной конвертации
с documented mm/intrinsics. Это частичная видимая поверхность, ручные точки
и непроверенная погрешность; quality unreliable. [Реальный пример](real_data_demo_ru.md).
[Texel](https://github.com/m-krivov/Texel-BodyScan-Dataset) остаётся data gate:
parser MIT, corpus CC BY-NC 4.0, unit готового vendor PLY не предполагается; в демо выбран raw depth с отдельной метрической проекцией.
Реальные данные в Git не добавлены. Open3D/Trimesh нужны для расширенных форматов,
здесь не устанавливались и не копировались. Исследование вариантов —
[report](../research/reuse/lidar/report_ru.md).

Внутренние позвонки, сколиоз/Cobb, источник боли и scanner capture не оцениваются.
