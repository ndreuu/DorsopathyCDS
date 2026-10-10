# УЗИ: calibrated PNG и ручная линия/площадь

`UltrasoundProvider`, module `ultrasound`, version `0.1.0`.
Initial endpoint — расстояние и площадь по authored points/contour внутри
заданного прямоугольника калибровки, без автоматической сегментации патологии.

## Input profile

Полная fixture [source.json](../tests/fixtures/ultrasound/source.json) и
[frame.png](../tests/fixtures/ultrasound/frame.png). Сначала загрузить PNG,
вписать его ArtifactRef ID в `frame_artifact_id`, затем загрузить JSON. В request
два input artifacts, parameters пусты. Input `ultrasound-manual.v1` сохраняет
structure/anatomy/side, acquired_at/clock, pose/protocol, coordinate frame,
calibration region и author/role ручных аннотаций.

Поддерживается один PNG frame=0: non-interlaced 8-bit grayscale/RGB/RGBA,
не более 1,048,576 pixels/8 MiB. PNG dimensions/CRC/decompressed length проверяются;
palette/16-bit/interlaced, другие raster types и DICOM pixel decode пока unsupported.
RGB/gray pixels сохраняются в overlay; RGBA отображается на чёрном фоне.
Declaration width/height обязана совпадать с **исходным** PNG. При resize/crop
нужно отдельно подготовить корректную calibration; importer не применяет
неизвестные display transforms к старому масштабу.

`calibration` содержит mm_per_pixel_x, mm_per_pixel_y, region `[x0,y0,x1,y1]`
(inclusive source pixel centers), status/source/author_id. Known scale требует
обе положительные осевые величины и bounds; software проверяет declaration,
а не метрологическую поверку. Unknown calibration даёт просмотр/разметку,
но measurement value=null/assessment_status=not_assessed. Весь line/contour
должен лежать внутри указанного региона. Разные scales по X/Y не усредняются.

## Numerical method и artifacts

- `ultrasound.distance`, mm: `hypot(dx*sx,dy*sy)`.
- `ultrasound.area`, mm2: shoelace area простого polygon × sx × sy.

Points `[1,1] → [4,5]` при sx=sy=1 дают 5 mm; rectangle
`[1,1],[4,1],[4,5],[1,5]` — 12 mm2. При sx=2, sy=3 получаются
`sqrt(6²+12²)` mm и 72 mm2. Self-intersections/duplicate vertices/outside points
отклоняются. Контур не должен повторять первый vertex в конце; замыкание неявное.

Artifacts: original PNG/JSON, отдельная authored annotation JSON, overlay SVG/PNG,
measurement JSON с pixel endpoints/contours/method/calibration. Contour в overlay
жёлтый, line cyan. Это **manual anatomy annotation**, не контур обнаруженной болезни.
Origin automatically_measured для записанных данных относится к арифметике,
не к авторству разметки; synthetic fixture остаётся synthetic_demo.
Comparison sidecar сохраняет time/pose/frame и calibration source evidence;
не добавляет MRI-body transform или clinical agreement.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_ultrasound.py' -v
```

## DICOM/real-export gates

`calibration_from_dicom_regions(regions,width,height)` — mapper уже декодированных
metadata: одна 2D tissue region, X/Y PhysicalUnits=3 (cm), конечные nonzero deltas,
source bounds. Возвращает `sx=10*abs(deltaX)`, `sy=10*abs(deltaY)` mm/pixel.
Это следует [DICOM PS3.3 2026d US Region Calibration](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.5.5.html),
где различные регионы кадра могут иметь независимые physical parameters.
Global PixelSpacing вместо region scale не используется. Mapper проверен на
synthetic decoded metadata; **DICOM worker/pixel decode не подключён**,
pydicom/Pillow в system environment отсутствуют.

Generic recorded PNG importer реализован. Настоящий lumbar PNG URS35_H3 из KU Leuven RDR проверен с опубликованными X/Y
калибровкой и crop; линия/ROI технические и ручные, не clinical ground truth.
[Реальный пример и ограничения](real_data_demo_ru.md).
[LUMINOUS](https://users.encs.concordia.ca/~impact/luminous-database/) — lumbar candidate,
но лицензия внешнего архива и per-image scale не верифицированы; 8 cm depth
не заменяет calibration. [Research report](../research/reuse/ultrasound/report_ru.md).
Пациентские/реальные data в Git не добавлены, vendor capture не реализован.

Thickness/area не диагностируют воспаление, саркопению, жировую фракцию или причину боли.
