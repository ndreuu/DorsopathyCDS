# МРТ: anatomy/replay и отдельный fresh worker

`MRIAnatomyProvider(live_provider=None)` имеет ID `mri.anatomy`, версию
`0.1.0`. Параметр `processing_mode` принимает `replay` (по умолчанию) или
`live`; другие параметры отклоняются. `live` делегирует доверенному
ProcessProvider; `close()` закрывает его. Платформа и source-grid provider
не импортируют NumPy/SimpleITK/pydicom/nibabel/Torch.

Первый результат — **геометрия анатомических масок**, а не диагностический
детектор. Concepts: `spine.canal.cross_sectional_area/mm2`,
`spine.canal.ap_extent/mm`, `spine.disc.mask_volume/mm3`. Канал не называется
отдельным дуральным мешком. Грыжи, степень стеноза, конус, корешки и
классификатор выпячивания остаются неоценёнными задачами.

## Проверяемый source-grid профиль

[Synthetic source](../tests/fixtures/mri_exports/source_grid_synthetic.json),
[request](../tests/fixtures/mri_exports/module_request.json) и
[result](../tests/fixtures/mri_exports/module_result.json) воспроизводимы.
`synthetic_source()` создаёт новую пиксельную матрицу и маску; 12 пикселей
канала на наклонённой ортонормальной плоскости с шагом 0.8×0.5 mm дают
4.8 mm² тем же вычислителем, что replay. Вход не содержит готового scalar.

Source JSON `mri.source-grid.v1` содержит ровно `schema_version`,
`source_kind`, `mode=synthetic_demo|recorded`, `processing=fresh|replay`,
`acquired_at`, `source={format,artifact_id,sha256}`, `registration_status`,
`model`, `provenance`, `comparison_metadata`, `frames`,
`volume_measurements`, `warnings`. Неизвестное время и comparison — null.
Recorded source ссылается на exact input ArtifactRef и его SHA256;
synthetic origin остаётся `synthetic_demo`.

Frame: `frame_index`, `frame_kind`, `geometry`, `image`, `mask`,
`image_artifact_id`, `mask_artifact_id`, `mask_kind:"anatomy"`, `structure`,
`measurements`. Geometry: `shape_rc`, `pixel_spacing_rc_mm`, `origin_lps`,
`row_direction_lps` (приращение ROW), `column_direction_lps` (COLUMN).
Матрицы либо inline, либо отдельные JSON artifact IDs с null inline.
Размер/тип/finite values, ортонормальность и положительный spacing проверяются.
Technical budgets: JSON ≤8 MiB, один frame ≤1M pixels, все frames ≤4M,
измерений ≤64, contour evidence ≤100k segments; boundaries вычисляются один
раз на frame. Это ресурсные ограничения, не медицинские пороги.
Шаг является калибровкой в mm, а не размером картинки на экране.

Каждая запись измерения содержит `measurement_id`, `concept`, `level`,
`numbering_status`, `assessment_status`, `quality`, `method`, `reason`,
`line`. CSA — count×pixel-area. Parent считает наибольший 8-connected
компонент; SpinoSarc сохраняет свой **all-mask-pixels** метод после linear
soft-mask interpolation/threshold 0.5 на native axial plane. Эти методы не
считаются взаимозаменяемыми. Пустой маске и компоненту на границе кадра
соответствует `not_assessed/null`. Непригодный sampling/oblique parent
не становится нулём или нормой. AP — длина явной непрерывной линии вдоль
patient AP, включая полувоксельные края. Unconfirmed numbering сохраняется
`needs_confirmation`.

Disc volume содержит sparse `voxel_indices_xyz`, полный `geometry`
(`shape_xyz`, `spacing_xyz_mm`, `origin_lps`, `direction`), exact
`mask_artifact_id`, method и при наличии `display_frame_index`. Объём
count×spacing product; 3D-сегментация остаётся связанной evidence. Это объём
метки в пределах покрытия, не проверенный полный анатомический объём.
Отдельная source-plane дисковая маска даёт визуальное evidence, не контур грыжи.

В output — scalar observations и отдельный `mri.measurements.v1` sidecar:
source/processing/provenance, методы, причины unknown, исходная геометрия,
PNG IDs, границы маски в pixel RC и physical LPS, AP endpoints. Source-gray,
mask и overlay PNG создаются из **одних** arrays без ручного обвода. PNG
остаются в source-grid ориентации; линейное display-window не заменяет raw MRI.
Evidence selectors: exact JSON pointer; measurement ID/frame index; PNG
image/frame/coordinate frame. `mask_kind=anatomy` не означает патологию.
`DICOM-LPS-mm` задаёт конвенцию осей, не совместную регистрацию разных серий.

## Реальные exports → upload bundle

`import_export(export_path, context, request, parent_root=None,
spinosarc_root=None) -> ModuleResult` — доверенный offline helper. HTTP не
принимает эти host paths. Parent profile: JSON `{series,analysis}` с
`series.volume_path`, `analysis.segmentation.segmentation_path` и исходными
`levels`. Bridge читает текущие volume/mask, проверяет source-grid geometry,
повторно вызывает parent `analyze_levels` и сверяет cached measured values.
Без связанных источников export отклоняется. Replay не запускает модель.

SpinoSarc `findings.json` поддерживается через его **неизменённые**
`prepare_volumes` и `resample_canal_to_axial_slice`. Native sources сверяются
по сохранённым size/mtime, NIfTI canal требует finite 3D/mm geometry, затем
CSA сверяется с текущей маской. Уровни вне axial coverage/approximate
сохраняются отдельными unknown observations. Registration `needs_review`
и старый inference returncode остаются в provenance. Muscle measurements,
пороговые narrowing flags и FF proxy этим первым адаптером не импортируются.

Из корня DorsopathyCDS, используя **уже существующее** imaging-окружение:

```sh
CDS="$PWD"
PARENT='/Users/andrew/.codex/worktrees/7179/med diploma'
PYTHONPATH="$CDS/src:$PARENT/backend/src" "$PARENT/.venv-spinosarc/bin/python" \
  -m dorsopathy_modules.mri_import \
  "$PARENT/var/spinosarc/analyses/20261008-032437-23b70c/findings.json" \
  --spinosarc-root /Users/andrew/Projects/SpinoSarc \
  --output-dir "$CDS/var/mri-replay-example"
```

Output directory должен быть новым. Получатся `bundle.json`, `result.json`,
`upload-manifest.json` и `artifacts/<sha>`. Для parent export вместо
`--spinosarc-root` использовать `--parent-root "$PARENT"`.
CLI выносит pixel/mask arrays в отдельные exact JSON artifacts. Helper
`bundle_source_arrays(data, context) -> (source, array_refs)` не меняет
геометрию или измерения и оставляет исходный словарь независимым.
Upload manifest перечисляет **relative** files/media types/SHA/size:
первым загружается bundle, затем все source/mask files. После upload
content-addressed IDs остаются теми же; input_artifact_ids идут в этом
порядке, processing_mode=replay. Source JSON не содержит host paths или UIDs.
Реальный public MRI с выдуманной анкетой требует `linkage:unlinked`,
а не клинически связанный эпизод.

## Fresh worker и проверенный запуск

Trusted command (argv tuple, не HTTP-параметр):

```sh
PYTHONPATH="$CDS/src:$PARENT/backend/src" "$PARENT/.venv-spinosarc/bin/python" \
  -m dorsopathy_workers.mri --exchange-dir EXCHANGE \
  --parent-root "$PARENT" --tss-python "$PARENT/.venv-tss/bin/python" \
  --weights-dir "$PARENT/var/totalspineseg" --device mps \
  --timeout-seconds 180 --contrast T2 --plane sagittal
```

EXCHANGE создаёт ProcessProvider с frozen request/input-file mapping. Вход —
один raw MHA/NIfTI/DICOM ZIP. Реиспользуется parent importer и bounded ZIP
boundary; classic MR поддерживается, enhanced/multiframe отвергается.
Для volume без acquisition metadata T2 здесь **явно декларируется** доверенной
конфигурацией; это профиль выбранной sagittal T2-серии, не автоопределение
любого исследования. DICOM contrast/plane не переписываются. Ровно одна
подходящая серия обязательна; unsupported/multiple series дают failure.

До inference worker заново хеширует bytes обоих checkpoints/plans/dataset,
проверяет manifest, pinned package 20260730 и release r20260730. Observation
model содержит **отдельный step2 checkpoint SHA**, остальные hashes —
provenance. Новый комбинированный SHA двух моделей не выдумывается.
Uploaded replay не может объявить самостоятельно проверенный model hash.
Один inference slot на стороне Dispatcher; здесь max-workers=1 и свой timeout.

9 октября 2026 выполнены два fresh jobs на public SPIDER 246:
первый корректно failed после 180.13 s, хотя MPS inference завершилась —
исходный Python CLI завис в atexit. После собственного subprocess shim
получен новый **fresh successful job**, 94.09 s, 15 observations: AP extent
и disc-mask volume на пяти уровнях; CSA unknown из-за native sampling.
Результат `partial` не означает ошибку или нормальную анатомию.

Shim `dorsopathy_workers.mri.tss_driver` загружает read-only parent script,
вызывает main; только нормальный return **integer 0** после его
`torch.mps.synchronize()` допускает flush/fsync logs и `os._exit(0)`.
Exception/nonzero/timeout успехом не становятся. Supervisor после выхода
убивает оставшиеся children одним group SIGKILL и независимо проверяет
segmentation geometry.
Provenance явно называет shutdown strategy; это **не** обычный exit upstream
CLI. Parent/SpinoSarc и installed packages/weights не редактировались.

Импорт без модели также проверен на реальных SPIDER MHA и public classic
DICOM. Parent разбил public sagittal DICOM на 15 single-frame blocks по своим
строгим geometry guards; мы не объединяем их молча. Native SpinoSarc replay
дает три измеренных axial CSA и два unknown уровня вне coverage. PNG overlay
проверен визуально на source frame. Все реальные данные, fresh logs/masks,
runtime/results находятся только в ignored var/private runtime, вне Git.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_mri*.py' -v
PYTHONPATH=src "$PARENT/.venv-spinosarc/bin/python" \
  -m unittest discover -s tests -p 'test_mri*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

В system Python optional parent geometry test может быть skipped; existing
imaging environment выполняет его полностью. Тесты проверяют физические
координаты/маску/PNG/evidence, known vs unknown, отсутствие ML imports,
cached-value/geometry mismatch, неизменённый replay и child shutdown.
Они не доказывают клиническую точность сегментации или диагноз.
