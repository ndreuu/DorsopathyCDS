# T03 — Реальный MRI-вход, экспортный адаптер и worker

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief, frozen worker/evidence formats T00 и текущие MRI-проекты read-only.

## Владение

Новые `src/dorsopathy_modules/mri_import/`, `src/dorsopathy_modules/mri_anatomy/`,
`src/dorsopathy_workers/mri/`, `tests/test_mri_import.py`, `test_mri_anatomy.py`,
`tests/fixtures/mri_exports/`, `docs/mri_provider_ru.md`.
Не меняй исходники parent/SpinoSarc, платформенные process/storage/HTTP/schema.
В Git только синтетические exports/fixtures; реальные MRI/маски остаются в var/data.

## Что переиспользовать

- Parent `/Users/andrew/.codex/worktrees/7179/med diploma`:
  `backend/src/spine_backend/imaging.py`, `segmentation.py`, `levels.py`,
  `lumbar.py`, `scripts/smoke_lumbar_http.py`, `scripts/render_lumbar_overlay.py`.
  Backend использует TSS step2 и формирует дисковые ROI; сохранённый итог
  `artifacts/lumbar-level-http-smoke.json` на момент аудита отсутствовал.
- `/Users/andrew/Projects/SpinoSarc`: `spinosarc_app/demo_io.py`, `lumbar_demo.py`,
  `demo/scripts/render_spinosarc_example.py`; связанные runtime через
  игнорируемый `.spinosarc.local.json`. step1 canal/landmarks и MuscleMap exports
  в parent `var/spinosarc/analyses/<run>/process-result.json`, `findings.json`.
- `run_spinosarc.py --example` показывает real cached example;
  `analyze_spinosarc_example.py` требует matching TSS cache. Эти команды
  не считать универсальным live-анализом любого нового исследования.

## Работа

1. Проаудируй реальные структуры exports и source linkage. Согласуй с T00
   синтетическую фикстуру, сохраняющую их форму; опиши adapter versions.
2. MRI import в отдельном worker-окружении: DICOM ZIP/MHA/NIfTI → серии,
   geometry, plane/contrast/coverage и source-frame references. Переиспользуй
   importer; QC/unsupported data не исправляй молча.
3. Replay bridge сохранённых findings/масок в ModuleResult. Проверяй совпадение
   источника, geometry/provenance; все evidence переносится в artifact store.
   Произвольный export без связанных изображений не считается полноценным replay.
4. Live worker через существующий parent headless pipeline — стартовый профиль
   для step2/ROI. Если выбран SpinoSarc-профиль, явно опиши его step1/measurement
   ограничения. T02 управляет процессом; твой worker только импортирует/анализирует
   и выдаёт согласованный manifest. Платформа не импортирует imaging/Qt/Torch.
5. Создай PNG исходных кадров/overlays и machine-readable evidence с координатами,
   контурами и линиями/методом измерения для T08. Маски не перерисовывать вручную.
6. Сохрани модель/версию/SHA при наличии verified metadata, параметры inference,
   source hash, источник серии, frame и numbering needs_confirmation.
   Непроверенный SHA не выдумывай; запроси верификацию у T00/T09.

## Приёмка

- Public DICOM-пример и SPIDER MHA проходят импорт без запуска модели.
- Ошибочная geometry, небезопасный архив, unsupported multiframe и отсутствующая
  подходящая серия дают корректные ошибки/partial, без patient identifiers в JSON.
- Replay не запускает inference; live делает свежий отдельный job на реальном MRI.
- Пригодное наблюдение открывает правильный source frame, маску и измерение.
  Валидация опирается на geometry/mask, а не только форму JSON.
- Native oblique canal measurements SpinoSarc и not_assessed parent oblique
  сохраняют исходный метод/ограничение; автоматической межсерийной регистрации нет.
- Канал/диск/мышца подписаны своей анатомией. Отдельный мешок, конус, грыжа,
  root compression и клиническая степень стеноза не объявляются реализованными.
- Пороговые SpinoSarc flags не превращаются в медицинскую степень стеноза;
  FF proxy не называется PDFF. Мышечный анализ можно импортировать отдельными
  observations, но это не обязательное условие первого live-прохода.

Один live-worker на Mac. Сдай коммит, exact commands и source manifest,
пример ModuleResult и доказательство fresh inference. Если запуск невозможен,
зафиксируй конкретный blocker, а не помечай cache/replay как live.
