# T11: готовые инструменты и открытые LiDAR/depth-данные

Подбор проверен 09.10.2026; ветка `research/reuse-lidar`, контракт ModuleResult 1.0, база `6161304de7c02960fa3d40e681ef612e90cd3e17`. Прочитаны AGENTS, brief, T11/T00, ports и схемы. Отдельный e2e freeze отсутствует; это интеграционный вопрос, не blocker исследования.

**Минимум:** Open3D + NumPy, PLY + JSON ручных внешних ориентиров. Сначала метрическая synthetic fixture, затем один Texel recorded export после проверки единиц. Optional Trimesh для GLB. Обучение и покупка оборудования не нужны. Ни один настоящий dataset ниже пока не прошёл runtime import/calibration.

## Инструменты

| Кандидат | Категория и точная entrypoint | Лицензии и пригодность |
|---|---|---|
| [Open3D](https://www.open3d.org/docs/0.19.0/tutorial/geometry/file_io.html), docs 0.19.0 | Generic geometry. `o3d.io.read_triangle_mesh(path)` / `read_point_cloud(path)`; PLY mesh/points, OBJ mesh, PCD points. [`VisualizerWithEditing.get_picked_points()`](https://www.open3d.org/docs/0.19.0/tutorial/visualization/interactive_visualization.html) возвращает manual vertex indices | [Код MIT](https://github.com/isl-org/Open3D/blob/b6c5e196384ad71e75b6e6f9c5da22d046221f1d/LICENSE); веса не нужны, данные лицензируются отдельно. Основной importer/picking, не body/posture pipeline |
| [Trimesh](https://trimesh.org/trimesh.exchange.load.html) | Generic mesh. `trimesh.load_mesh(path, process=False)`, [`trimesh.exchange.gltf.export_glb(scene)`](https://trimesh.org/trimesh.exchange.gltf.html); PLY/OBJ/GLB | [MIT](https://github.com/mikedh/trimesh/blob/dc8490c825c382d2d58b5bd87ecacc74790ead27/LICENSE.md); без весов. Optional viewer adapter; сохранять source indexing/transform |
| [CloudCompare](https://www.cloudcompare.org/doc/wiki/index.php/Point_picking) | Generic desktop QC. `Tools > Point picking`: coordinates/index; две точки — distance/dX/dY/dZ, три — углы | [GPL-2.0-or-later](https://github.com/CloudCompare/CloudCompare/blob/master/license.txt); без весов. Независимая ручная проверка отдельным приложением |
| [SMPL-Anthropometry](https://github.com/DavidBoja/SMPL-Anthropometry) | Готовый **body measurement pipeline**: `MeasureBody("smpl")`, `from_verts(torch.tensor(verts))`, `measure(["height", "shoulder breadth"])`; [точные keys](https://github.com/DavidBoja/SMPL-Anthropometry/blob/d8f43bca7a2ae6263f43ebb0efe74136e37e25eb/measurement_definitions.py) | [Wrapper MIT](https://github.com/DavidBoja/SMPL-Anthropometry/blob/d8f43bca7a2ae6263f43ebb0efe74136e37e25eb/LICENSE); [body assets отдельно non-commercial](https://smpl.is.tue.mpg.de/modellicense.html), регистрация/запрет redistribution. Только exact SMPL 6890 / SMPL-X 10475 vertices и neutral T-pose; arbitrary PLY не подходит |

У SMPL `from_verts` всё равно загружает licensed faces/joint regressor; [исходник](https://github.com/DavidBoja/SMPL-Anthropometry/blob/d8f43bca7a2ae6263f43ebb0efe74136e37e25eb/measure.py) предполагает metres, выдаёт cm. Это условный будущий профиль, не минимальная зависимость. Height-normalization не подтверждает calibration.

## Данные: verified и unknown отдельно

**Texel Part1 — предпочтительный recorded кандидат.** [Авторский README](https://github.com/m-krivov/Texel-BodyScan-Dataset/blob/5b3ba6fa1606e92205c3bdebde53b401a8c999a6/README.md) описывает 10 настоящих людей в белье: `portal_mx/scan.ply`, `free_fusion/scan.ply`, `frames/*.png` uint16, camera XML/JSON, measurements CSV. Yandex API подтвердил [Texel-BodyScan-Part1.7z](https://disk.yandex.ru/d/5R57d5509rP7jQ), **140327895 bytes**. Архив не распаковывался: exact members/import неизвестны. [Parser MIT](https://github.com/m-krivov/Texel-BodyScan-Dataset/blob/5b3ba6fa1606e92205c3bdebde53b401a8c999a6/LICENSE-CODE) отдельно от [данных CC BY-NC 4.0](https://github.com/m-krivov/Texel-BodyScan-Dataset/blob/5b3ba6fa1606e92205c3bdebde53b401a8c999a6/LICENSE-DATA); обязательны attribution TEXEL и disclosure изменений, commercial use не разрешён. Веса для raw-surface не нужны; parametric assets имеют отдельные условия.

**PLY unit неизвестна.** Kinect DEPTH16 номинально mm по [Microsoft header](https://github.com/microsoft/Azure-Kinect-Sensor-SDK/blob/develop/include/k4a/k4atypes.h); сохранение единицы в Texel PNG и intrinsics после undistortion/crop ещё проверить. [Texel camera offset](https://github.com/m-krivov/Texel-BodyScan-Dataset/blob/5b3ba6fa1606e92205c3bdebde53b401a8c999a6/utilities/Defs.h) — metres, что не доказывает PLY-scale. `./bin/IterateScans <directory>` читает metadata, не реконструирует body; meshes/measurements игнорирует. Free Fusion добавляет synthetic model-derived участки, поэтому provenance поверхности смешанный. Первый выбор — portal mesh либо подтверждённый raw-depth ROI.

**HSRD-100 — conditional.** [Provider card](https://huggingface.co/datasets/digitalrealitylab/HSRD-100/blob/9cc5e9c138dd310dc88c95c80e5db0a3ecb7971a/README.md) заявляет CC BY 4.0; API verified public/ungated, revision `9cc5e9c138dd310dc88c95c80e5db0a3ecb7971a`. Реальный [Scan-LOD2.zip](https://huggingface.co/datasets/digitalrealitylab/HSRD-100/blob/9cc5e9c138dd310dc88c95c80e5db0a3ecb7971a/data/HSR0027/HSR0027-Body-006/scans/HSR0027-Body-006-Scan-LOD2.zip) — **22918198 bytes**. Bounded Range, 65557 bytes, HTTP 206: directory подтверждает `HSR0027-Body-006_LOD2.obj` + PNG/JSON. Соседние 41-byte `...-LOD2.zip` не выбирать.

[Person](https://huggingface.co/datasets/digitalrealitylab/HSRD-100/blob/9cc5e9c138dd310dc88c95c80e5db0a3ecb7971a/data/HSR0027/HSR0027-Body-006/metadata/person_meta.json) и [pose metadata](https://huggingface.co/datasets/digitalrealitylab/HSRD-100/blob/9cc5e9c138dd310dc88c95c80e5db0a3ecb7971a/data/HSR0027/HSR0027-Body-006/metadata/pose_meta.json) выбранного объекта содержат `license: "Commercial"`; смысл относительно card CC BY не объяснён. Это неоднозначность, не доказанный запрет. До уточнения — conditional. Vertex units отсутствуют; height без единицы недостаточен. Crossed Arms ограничивает coverage. Это реальный photogrammetry body, не подтверждённый LiDAR capture.

## Интеграция и измерения

Предложение input profile `surface_geometry_manual_v0.1`: source PLY; geometry/session JSON с unit-to-mm или null, scale authority, axes/handedness/frame/transform, pose/coverage/clothing, acquisition time/timezone или null, linkage; отдельный landmarks JSON с author/role/time, source SHA, coordinates и vertex/triangle reference, повторяемостью или null.

`ProviderProtocol.analyze(request, context)`; proposed module `lidar.posture`. Большие файлы через `context.artifacts.open(ref)` → bounded copy в worker-local path; outputs через `add_file(Path, media_type, role="derived")`. ArtifactRef сохраняет ID/storage_key/SHA/media_type/size/role. Metadata не добавляет новых Observation fields.

Для scale s в mm: distance = `s*norm(B-A)`; height difference = `s*dot(B-A,up)` при verified up; angle ABC = `atan2(norm(cross(A-B,C-B)),dot(A-B,C-B))*180/pi`, zero arms запрещены. Proposed concepts `surface.landmark.distance` mm, `height_difference` mm, `angle` deg согласовать T00. Угол трёх точек не требует physical scale; projected posture angles требуют body frame.

Evidence: source + authored landmarks + definitions/QC JSON + viewable PLY/optional GLB + PNG с overlay/camera. PNG: [`OffscreenRenderer.render_to_image()`](https://www.open3d.org/docs/0.19.0/python_api/open3d.visualization.rendering.OffscreenRenderer.html), затем `o3d.io.write_image`; target-runtime smoke pending. Реальная арифметика `automatically_measured`, model null; synthetic только `synthetic_demo`. Неизвестное acquired_at null; external anatomy без vertebral level. `result_envelope` + `validate_result`; standalone result объявляет все evidence artifacts.

## QC, gates и effort

- Unknown scale: mm not_assessed/null, result partial. Missing landmarks/coverage — partial; invalid geometry — failed/error без observations.
- Проверять finite/empty geometry, faces, source-hash/frame consistency, uniform positive scale, rigid transform, visibility/clothing, zero arms; не smoothing/hole-filling измерительный source без оценки смещения.
- Missing calibration: unit authority конкретного экспорта, intrinsics/crop audit, reference residual, repeat scans/annotations, acquisition clocks. Resolution не равна accuracy; неизвестную ±error не придумывать.
- Synthetic gate ещё не реализован. Независимый Python math подтвердил triangle 300/400/500 mm, 90 deg; perturbation B.z+=2 даёт 89.61803379527097 deg. При endpoint norm-error ≤ε distance error ≤2ε; это software bound, не scanner accuracy.
- Real import gate pending extraction/units/QC. Device capture **недоступен**: нет выбранного/доступного прибора, SDK/firmware/OS-profile и calibration protocol; recorded processing не называется capture.
- 3–5 рабочих дней без обучения: profile/fixture → importer/QC → landmarks/arithmetic → provider/evidence → worker/render. T00 concepts/selectors, T01 artifacts/MIME, T02 exchange/resource slots, T08 viewer; ожидание data clarification отдельно.

Внешняя поверхность не определяет внутренние позвонки, Cobb, диагноз, причину боли или проверенную MRI-registration. Body corpora и synthetic tests не clinical validation. Только research-файлы: библиотеки не устанавливались, веса/полные datasets не скачаны, provider tests не запускались.
