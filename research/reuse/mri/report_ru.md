# MRI reuse: T03 и ближайший T10

Проверено 09.10.2026 (Europe/Istanbul). Только подбор и read-only аудит; установки, downloads весов, hashing checkpoints и inference не выполнялись. **Минимум сейчас: существующий importer → полный TotalSpineSeg step2 → исходные кадры, анатомические маски/ROI и геометрические измерения → готовый renderer.** SpinoSarc добавляет native axial canal CSA и мышцы. Автоматического собственного патологического классификатора нет; ROI диска не является контуром выпячивания.

## Четыре кандидата

| Кандидат | Реально готовый output / input | Права и решение |
|---|---|---|
| [TotalSpineSeg](https://github.com/neuropoly/totalspineseg) | NIfTI 3D разных MRI contrasts/orientations; step2 — именованные позвонки, диски, cord/canal masks; предполагаемый level, без side/pathology category. Source-grid output по умолчанию; step1 landmarks не заменяют полную маску диска. | Код LGPL-3.0; отдельная лицензия весов не подтверждена. Выбрать для анатомии. [Релиз r20260730](https://github.com/neuropoly/totalspineseg/releases/tag/r20260730) публичен; обучен также на SPIDER и внутренних данных. |
| [SpinoSarc](https://github.com/neuromath/SpinoSarc) | DICOM/NIfTI; sagittal для TSS, native axial для CSA/8 bilateral muscle classes. Обратное проецирование canal mask на исходную oblique axial plane; nearest-slice distance/gaps сохраняются. JSON/PNG/PDF и GUI Excel. | MIT-код; TSS права отдельно. MuscleMap abdomen v0.0 имеет MIT в локальном manifest/отдельном LICENSE, [record](https://zenodo.org/records/19631081) в текущем web не открылся. Выбрать существующий fork как дополнительный measurement/export путь. Название upstream «dural sac» не меняет источник step1_canal. |
| [SpineReport](https://github.com/ivadomed/SpineReport) | NIfTI+BIDS names и TSS 1-mm isotropic outputs; CSV, PNG и reports: canal area/AP/RL, disc volume/median_thickness, foraminal morphometry. Levels/стороны — анатомические; отдельного pathological detector нет. | LGPL-3.0; собственные веса не требуются, права TSS наследуются. Резерв измерителей, перенос transforms/source-frame нужен. [measure_seg.py](https://github.com/ivadomed/SpineReport/blob/main/spinereport/utils/measure_seg.py) собирает canal из SC+CSF; median_thickness не передняя/средняя/задняя высота. |
| [SpineNet V2](https://github.com/rwindsor1/SpineNet) | Sagittal H×W×S MRI+pixel spacing; localization на T1/T2/STIR, grading только lumbar sagittal T2 T12/L1–L5/S1. DataFrame: 11 голов, включая binary Herniation, central stenosis, left/right foraminal stenosis, Pfirrmann/narrowing. Vertebral polygons/IVD crops, без маски/размеров грыжи и без bulging head. | [Условия](https://github.com/rwindsor1/SpineNet/blob/main/LICENCE.md) охватывают код, веса и outputs: только non-commercial research; коммерческий продукт требует другой лицензии. Веса публикуются VGG; доступность bytes/hash не проверена. Условный PoC категории, не готовый T10 bulging. [Paper](https://arxiv.org/abs/2205.01683) не доказывает полностью открытые training images. |

CPU/CUDA предусмотрены TSS/SpineNet; SpineNet CPU медленнее, MPS/runtime/RAM у нас неизвестны. SpinoSarc source ориентирован на Apple Silicon/macOS, локальный MPS adapter использует CPU fallback отдельных операций, оригинальные plans; GPU training requirements не считать inference requirements. SpineReport измерители CPU; полный pipeline включает TSS. Fresh full-step2 время/память не измерены. Локальный сохранённый SpinoSarc step1 занял 64.38 s, но `actual_process_returncode=-15`, teardown восстановлен: это прежний artifact, не свежий smoke/нормальное завершение нового запуска.

## Повторное использование и точные входы

Из parent (commit и hashes в JSON), после подготовки NIfTI importer:

```sh
.venv-tss/bin/totalspineseg input.nii.gz OUT --data-dir var/totalspineseg --device cpu --max-workers 1 --max-workers-nnunet 1 --quiet --keep-only step2_output
.venv-tss/bin/python scripts/totalspineseg_mps.py input.nii.gz OUT --data-dir var/totalspineseg --device mps --max-workers 1 --max-workers-nnunet 1 --keep-only step2_output
python3 scripts/smoke_lumbar_http.py --image data/spider/images/246_t2.mha --base-url http://127.0.0.1:8000
.venv/bin/python scripts/render_lumbar_overlay.py --image INPUT.mha --segmentation MASK.mha --output OVERLAY.png
```

Это будущие команды, не выполненные проверки. `imaging.import_images(input_path, output_dir)` → `lumbar.analyze_lumbar_series(series, output_dir, config)`; REST `POST /studies`, `POST /studies/{id}/analyses`, `GET /analyses/{id}`, `GET /analyses/{id}/artifacts/{artifact_id}`. Parent сохраняет LPS geometry, source hashes/frames, segmentation MHA, disc crops/masks и measurement endpoints; oblique measurements могут остаться `not_assessed`.

В `/Users/andrew/Projects/SpinoSarc`: `python3 demo/scripts/run_spinosarc.py` открывает реальный ввод; `--example` загружает cached example. `demo/scripts/analyze_spinosarc_example.py` запускает мышцы, но требует matching TSS cache — это смешанный pipeline. `demo/scripts/render_spinosarc_example.py --findings FINDINGS.json --output OUT.png` воспроизводит saved masks. `python -m spinosarc_app.cli analyze SLICE.nii.gz --musclemap MM_SCRIPT --output-dir OUT --cpu` — односрезовый путь, не универсальный многосерийный worker. Exports: `process-result.json`, `findings.json`, `muscle_frames`, masks, per-level measurements; `.spinosarc.local.json` указывает parent runtime.

Upstream: `totalspineseg RAW TSS --iso`; `spinereport -t TSS -c CONTROL -o reports` (контрольная группа не клиническая норма). SpineNet: `spinenet.download_weights()`; `s=spinenet.SpineNet(device='cpu')`; `v=s.detect_vb(volume,pixel_spacing)`; `i=s.get_ivds_from_vert_dicts(v,volume)`; `s.grade_ivds(i).to_csv(...)`. Собственная подготовка IVD обязательна: произвольный TSS crop несовместим без проверки.

## Данные, пробелы, ближайшее решение T10

[SPIDER v4](https://zenodo.org/records/10159290): 218 public patients/447 sagittal T1/T2 series, MHA vertebra/disc/canal masks; CC-BY-4.0. CSV per-IVD: binary bulging/herniation/narrowing, Pfirrmann, Modic, endplate, spondylolisthesis. [Provider](https://spider.grand-challenge.org/data/) подтверждает caudal-relative numbering, не гарантированное L4–L5. Нет pathological material masks/side/root involvement/herniation millimeters. Local manifest: 64 patients, 447 reference ROI, bulging 216+/231−, herniation 20+; train/development/test patients 40/13/11, ROI 276/92/79. Это oracle localization; scores ещё нет.

[RSNA/LumbarDISC](https://pubs.rsna.org/doi/10.1148/ryai.250480): DICOM sagittal T1, sagittal T2/STIR, axial T2; пять уровней × canal/двусторонний foraminal/двусторонний subarticular severity (normal/mild, moderate, severe) и coordinate annotations; нет bulging/herniation masks. [Текущий provider](https://registry.opendata.aws/rsna-lumbar-spine-degenerative-classification-dataset/) — controlled access, non-commercial, без redistribution; не «безусловно открытый» корпус. Локальное наличие/доступ не подтверждены.

Вывод: T10 binary bulging потребует обучения на имеющихся CSV, freeze patient split и проверки joins; дополнительная binary-разметка для текущего baseline не нужна. Для патологического контура/стороны/размеров нужны отдельные эталоны и сегментатор; для отдельного dural sac — целевая модель/валидация. SPIDER test не независим от pretrained TSS: фиксировать inherited exposure, отдельно reference vs predicted ROI и внешний cohort. Gates: weights rights, source/geometry/frame linkage, axial coverage/resolution, numbering `needs_confirmation`, registration `needs_review`, missing=`unknown/not_assessed`. Следующий smoke — один fresh job с new output/log, нормальным exit, runtime/packages/hashes и проверкой source-grid overlays; replay остаётся replay.
