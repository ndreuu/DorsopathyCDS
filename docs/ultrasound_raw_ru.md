# RAW-US: исходное УЗИ → модельная маска → измерения

`RawUltrasoundProvider`, module `ultrasound`, version `0.2.0`; прежний ручной
`UltrasoundProvider 0.1.0` сохранён. Provider не импортирует PyTorch/pydicom;
импорт DICOM и модель запускаются в `dorsopathy_workers.ultrasound` через
`ProcessProvider`. Автоматическая пригодность анатомической плоскости и
клиническая точность этого программного пути пока не подтверждены.

## Вход

[source.json](../examples/ultrasound_raw/source.json) — точный профиль
`ultrasound-raw.v1`, два source artifacts: manifest JSON и исходный PNG/DICOM.
`parameters={}`. `frame_artifact_id` связывается с загруженным изображением.
`structure=lumbar_multifidus`, `anatomy.region=lumbar_multifidus`; сторона может
быть `unspecified`. Один выбранный экземпляр не выдаётся за bilateral mask.
`plane=transverse|longitudinal|unknown` — сведения протокола получения, а не
результат классификатора. `frame_index=null` включает технический выбор кадра;
целое неотрицательное значение выбирает исходный кадр. Маски, точки, polygon,
ROI, путь к модели и другие дополнительные поля в upload запрещены.

Сохраняются время/clock (в том числе `null`), pose, protocol, subject/session,
source pixel axes, сторона/уровень/numbering. Они не восстанавливаются по
яркости изображения. Синтетический вход остаётся `synthetic_demo`.

PNG: проверяются CRC, dimensions, decompressed length; поддержаны 8-bit
gray/RGB/RGBA, non-interlaced, до 1,048,576 pixels/16 MiB. Verified calibration
требует X/Y mm-per-pixel, integer bounds `[x0,y0,x1,y1]` исходных pixel centres,
источник и автора. Пользовательское изменение масштаба/crop требует нового
corresponding calibration; незаявленный resize не применяется.

DICOM: до 256 MiB, 256 frames, 2,100,000 pixels/frame; `Modality=US`,
MONOCHROME2 или uint8 RGB. `pydicom.pixels.pixel_array(...,index=...)` декодирует
кадр. В manifest calibration остаётся `unknown`: известный масштаб получается
только из одной 2D tissue region, SpatialFormat=1, DataType=1, X/Y units=3(cm),
finite nonzero deltas, допустимых bounds. `sx=10*abs(deltaX)`,
`sy=10*abs(deltaY)`. Global PixelSpacing не заменяет US Region Calibration:
[DICOM PS3.3 C.8.5.5](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.5.5.html).
Неоднозначные/пропущенные region tags оставляют метрику unknown. Compressed
syntax требует поддержанного внешнего decoder; неподдержанный decode явно
проваливается. Для cine выбирается максимальный robust intensity range после
декодирования среди до 16 равномерно взятых кадров. Это технический QC;
анатомическую плоскость/полноту мышцы он не подтверждает.

## Реальная модель и лицензии

Выбран кандидат [UltraSAM3](https://github.com/zhuqh19/UltraSAM3), commit
`7ab334166fee7f87cf47a213d97731ad482dad3f`. Публичный ungated
[checkpoint](https://huggingface.co/zhuqh19/UltraSAM3/tree/6cdd74d0c741c411b09d2d7c7b4703f003837c25)
содержит native image model и optimiser: 10,081,250,438 bytes; ожидаемый
полный SHA256 `b57a47ecca055472191a4460aba1adb7f1d29526b375efae67c98e463225b63d`.
Native model имеет 1,134 state entries, 841,689,398 parameters/buffers,
3,371,476,184 tensor bytes. Сверены все имена/формы с released metadata.
Точный training concept из official LUMINOUS converter: `multifidus_muscle`.
Никакие geometric prompts модели не передаются.

[Preprint 2607.29200v1](https://arxiv.org/html/2607.29200v1), Table 6, сообщает
LUMINOUS IoU 0.7512/Dice 0.8420. Это результат авторов на корпусе, включённом
в их разработку, не независимая проверка нашего KU Leuven кадра и не
диагностическая валидация. [LUMINOUS provider](https://users.encs.concordia.ca/~impact/luminous-database/)
даёт поясничные multifidus images/masks; право использования внешнего архива
и per-frame scale остаются отдельными условиями. Архив LUMINOUS не переносится
в этот repository. Generic SAM/MedSAM с ручным box эту автоматизацию не заменяет.

HF card заявляет Apache-2.0, но [bundled code/LICENSE](https://github.com/zhuqh19/UltraSAM3/blob/7ab334166fee7f87cf47a213d97731ad482dad3f/code/LICENSE)
содержит SAM License от 19.11.2025. Отдельной лицензии внешнего inference wrapper
нет. Поэтому blanket-заявления «всё Apache» нет; upstream code/weights здесь
не распространяются. Worker использует native image API, не wrapper trainer.

На CPU upstream files не изменяются. Явный bridge: unused tracking EDT/Triton
и video/decord imports получают guards, **которые выбрасывают ошибку при
вызове**; никаких заменяющих tensors они не возвращают. Отключён необязательный
CUDA positional-cache warmup (обычная lazy формула остаётся), decoder
coordinate-cache выделяется на CPU с той же формулой. Bridge/hash, upstream
commit, device/PyTorch, пороги и mask selection записываются в analysis artifact.
В geometry encoder на CPU пропускается только `scale.pin_memory()`:
точный pinned метод воспроизводится с одной заменой, `scale.to(device)` и
вычисления остаются теми же. Это устраняет конфликт CPU/MPS storage у
Apple PyTorch 2.5.1, не заменяет тензоры и не меняет upstream files.
Native state loading запрещает missing/unexpected keys; `weights_only=True`
и `mmap=True` уменьшают память. CPU architecture build проверен; полноценный
inference и его результат описываются ниже отдельно.

## Загрузка и запуск

В отдельном optional environment: [requirements-ultrasound.txt](../requirements-ultrasound.txt).
Torch/torchvision должны быть совместимой парой для host. Не изменять MRI env.

```sh
<ultrasound-python> scripts/fetch_ultrasound_model.py \
  --output-dir var/ultrasound-model --mode image --workers 6
```

Fetcher возобновляет verified byte ranges из официального pinned release.
`--mode image` получает **все реальные native model storages**, проверяет
original ZIP CRC каждого и pinned pickle/central-directory SHA, создаёт
`UltraSAM3-image.pt` без optimiser. Missing storages не заполняются нулями.
`image-weights.json` содержит actual extracted checkpoint SHA, SHA каждого
storage, ranges и независимый детерминированный digest state:
length-prefixed canonical JSON по sorted key, shape, stride, offset, dtype,
storage SHA. Полный upstream SHA при таком способе **не считается проверенным**.
Перед модельной загрузкой worker независимо сверяет SHA сохранённого
`released-model-metadata.pkl`, все key→storage/count/offset/shape/stride,
requires-grad/hooks с оригиналом и пересчитывает state digest из actual bytes.
Same-shape перестановка storages не принимается. Контейнер использует
unsigned 32-bit ZIP offsets до 4 GiB: ранний ZIP64 после 2 GiB, который создаёт
Python по умолчанию, несовместим с native reader на этой сборке PyTorch.
`--mode full` проверяет SHA всех 10 GB; `--mode source` получает только source.
Все assets сохраняются в ignored `var/`, не в Git.

```sh
PYTHONPATH=src <ultrasound-python> scripts/run_ultrasound_raw.py \
  --frame <raw-frame.png-or.dcm> --manifest <acquisition.json> \
  --worker-python <ultrasound-python> --output-dir var/ultrasound-raw-result \
  --upstream-root var/ultrasound-model/UltraSAM3-7ab334166fee7f87cf47a213d97731ad482dad3f \
  --checkpoint var/ultrasound-model/UltraSAM3-image.pt --device cpu
```

[worker-config.json](../examples/ultrasound_raw/worker-config.json) соответствует
aggregate trusted `--raw-workers-config` интегратора. Absolute source PYTHONPATH,
host model paths, device и timeout задаются оператором; uploads их не управляют.
Без model args выполняются import/QC, а оба признака получают
`not_assessed/model_not_configured`.
Повторное использование output directory удаляет прежние convenience
PNG/JSON до нового запуска; отсутствующая новая маска не оставляет старый
overlay рядом с текущим result. Для сравнения запусков использовать разные
output directories; ArtifactRefs остаются content-addressed.

## Выходы и границы

`ultrasound.muscle.cross_sectional_area`, mm2: число mask pixel cells × sx × sy,
только при declared transverse plane. `ultrasound.muscle.thickness`, mm:
median вертикальной непрерывной mask extent в центральных 20% её ширины,
с pixel-cell краями `y±0.5`. Это воспроизводимая extent вдоль image Y,
не подтверждённая толщина между фасциями. Representative line голубая,
model mask жёлтая в source overlay. Mask restored to original dimensions;
source/image/mask/overlay и анализ имеют ArtifactRefs/evidence.

Empty, low-score (<0.5), clipped/region-outside/disconnected masks, неизвестный
масштаб или плоскость не превращаются в ноль. Все методические/анатомические
качества остаются `quality=unknown`; score не является калиброванной
вероятностью диагноза. Сторона и уровень не определяются по top-1 mask.
Статический выбранный кадр не измеряет сокращение/динамику/асимметрию.
Размер не диагностирует атрофию, воспаление, миофасциальную боль или причину
дорсопатии. Эти observations не активируют клиническое диагностическое правило.

## Проверка

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_ultrasound_raw*.py' -v
PYTHONPATH=src <ultrasound-python> -m unittest discover -s tests -p 'test_ultrasound_raw*.py' -v
```

15 tests проходят с optional deps: geometry/QC, absent model/calibration,
clipping/disconnection, запрет authored mask/ROI/host paths, isolated protocol,
raw DICOM multiframe decode/selection/region units, конфликт PixelSpacing,
pickle security, value-preserving model-only serialization, sparse >2 GiB
record offsets, same-shape storage swap/digest rejection и отсутствие stale
previews после повторного/failed запуска. Без optional deps
соответствующие DICOM/Torch проверки явно skipped. Synthetic checks проверяют
программный контракт и арифметику, не реальную точность сегментатора.

Реальный KU Leuven кадр `URS35_H3/HUS/0.png`, calibration и inference artifacts
остаются в `var/real-inputs/ultrasound-raw/`. Источник:
[KU Leuven RDR doi:10.48804/3XPCAE](https://rdr.kuleuven.be/dataset.xhtml?persistentId=doi:10.48804/3XPCAE),
CC BY 4.0; опубликованная геометрия crop/scale, без ручного anatomical ROI.
Scan LR и расположение probe параллельно spine взяты из acquisition protocol,
[Scientific Data Figure 4/Methods](https://www.nature.com/articles/s41597-025-06047-9).
Frame-level side/level/time и clinical multifidus review не восстановлены.

11.10.2026 выполнен fresh CPU inference released image model на исходном
публичном кадре 390×780: 2 кандидата, top score 0.6463377 (некалиброванный),
native forward около 9.6 s. Маска восстановлена в исходной геометрии;
overlay осмотрен, но анатомическая точность экспертом не подтверждена.
Автоматическая маска содержит 14,080 pixel cells и две 4-connected компоненты
размером 14,077 и 3. Поэтому **area и thickness остались not_assessed** с
причиной `disconnected_mask`; мелкая компонента не удалялась для получения
числа. Кроме того, declared longitudinal plane не допускает CSA.
Calibration sx=0.0949559, sy=0.09310205 mm/pixel взята из опубликованной
строки `URS35_H3` scale CSV. Side/level/time и clinical multifidus review
остаются unknown. Готовность проверена для реальной автоматической
сегментации и программного измерительного/QC пути; пригодность этого кадра
для численной оценки и клиническая точность не заявлены.

Actual image checkpoint: 3,371,840,085 bytes, SHA256
`d18fe5ec5c0f16565285415a418ec3f63089d07e3bf281e7a57469255b1aaba8`.
Canonical model-state SHA256
`ee534ab5312ae4f401a0011c2bee2de0c169d01913ea90966384a1fee976e8a4`;
все 1,134 реальные storages сверены с original CRC, per-storage/range SHA
записаны в ignored `image-weights.json`. Полный 10 GB hash не проверен.
Артефакты финального standalone прогона:
`var/real-inputs/ultrasound-raw/run-verified/{result,analysis}.json`,
`source.png`, `mask.png`, `overlay.png`. Их байты/refs/hash отражаются в
result/analysis; данные и веса не включаются в Git.

Предыдущий HF Xet transfer 10.10.2026 завершился сетевыми timeout/reset;
неполный upstream файл никогда не использовался для измерений.
