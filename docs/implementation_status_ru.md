# Реализация мультимодального MVP

Рабочая ветка: `feature/multimodal-mvp` в `/Users/andrew/Projects/DorsopathyCDS`.
Исследовательские результаты остаются в `research/reuse/`. Код программы — `src/`.
Дата: 9 октября 2026 года.

## Владение и рабочие места

Freeze: `b6bf09e`, ModuleResult 1.0 без изменений. Root также владеет workflow,
Application, HTTP/viewer, bootstrap/CLI, packaging и общей приёмкой.
В сессии не более трёх исполнителей одновременно; новые задачи получают
освободившиеся слоты. Агенты не редактируют чужие файлы.

| Направление | Владелец | Worktree в DorsopathyCDS-agents | Ветка |
|---|---|---|---|
| T01 storage/uploads | implement_storage | T01-storage | feature/mvp-storage |
| T02 execution | implement_execution | T02-execution | feature/mvp-execution |
| T04 intake | implement_intake | T04-intake-mvp | feature/mvp-intake |
| T03 MRI | implement_intake | T03-mri-mvp | feature/mvp-mri |
| T07/T16 report/comparison | implement_storage | T07-report | feature/mvp-report |
| T11/T12/T14 spatial measurements | implement_execution, следующая задача | T11-spatial | feature/mvp-spatial |

## Принято в общую ветку

- T01: `a5d9b37`, source `1d45db6`: каталог, bounded uploads, migrations,
  atomic run+jobs, CAS finalization.
- T04: `7c6a7c5`, source `ee9e773`: complaints/clinical-exam providers,
  evidence, origin/states, exact reviewed MRI artifact.
- T02: `72ecf90`, source `d055948`: dispatcher, recovery, ProcessProvider,
  private verified file exchange, process-group cleanup.
- Общий workflow/HTTP/viewer: `54ba5d6`; совместный набор 193 tests PASS.

- T11/T12/T14: `c894368`, source `9824f33`: калиброванные LiDAR/thermal/US
  профили, численные измерения, геометрия/ROI, PNG/SVG и evidence.
- T07/T16: `e6373ca`, source `cabc7ce`: проверяемый snapshot adapter,
  frozen clinical report и отдельная проверка технической совместимости.
- T13/T15: `4ad6d7c`, source `b199462`: CSV EMG RMS/MDF и canonical index;
  пропуски, clipping и bounded spectral work, PNG/SVG/JSON.

- T03: `9610c40`, source `0ba2f61`: source-grid/replay нормализатор, linked pixels,
  externalized arrays, отдельный verified fresh worker и guarded subprocess shutdown.
- Исправление области EMG: `c16242a`, source `2d821a0`.

Финальный bootstrap и сквозная приёмка фиксируются интеграционным коммитом.
Инструкция пользователя: [полный запуск](run_full_flow_ru.md).

## Evidence port для отчёта

Application передаёт readonly EpisodeArtifactReader: read(ref), open(ref),
get_artifact(episode_id,artifact_id). Каждый ref сверяется с каталогом того же
эпизода; содержимое открывается с проверкой SHA/size. Клиентский manifest не
является источником storage_key. Дополнительная ссылка linkage attestation
разрешается этим портом, а не поиском произвольного файла на диске.

verified_linked использует профиль linkage-attestation.v1: episode/subject/session,
author/assessment_ref и source artifact IDs с SHA. Проверяются связи файлов и
известные противоречия происхождения. Это не проверка личности/квалификации автора
и не клиническая валидация связанного корпуса.

## Что подтверждает сборка

Первый профиль сенсоров — статические калиброванные входы и записанные экспорты.
Камеры/сканер/ANI не подключены автоматически. Поддержка конкретного vendor export,
настоящий импорт и аппаратный capture имеют отдельные статусы.
Новый MRI inference выполняется только через явно настроенный trusted worker;
без него replay остаётся replay. Необученный дисковый classifier не регистрируется.


## Проверки интеграции

Полный учебный flow запускает восемь production providers на синтетических исходных
данных; численные значения проверяются по геометрии/сигналу. Первый clinical report
indeterminate, следующий после отдельной synthetic clinical review — met; MRI result
ID сохраняется, первый отчёт не меняется. Unlinked reuse остаётся indeterminate.
Failure одного модуля сохраняет остальные результаты; статус учитывает reused results.

HTTP имеет raw streamed upload, scoped verified download, loopback Host/Origin gates,
безопасные коды ошибок. Runtime lock не допускает восстановления заданий другим
контроллером в store действующего сервера. UI защищает текущий episode/run от ответов
устаревших запросов. Подключение registry не импортирует torch/SimpleITK/nnU-Net/Qt.

Независимый агент проверил интеграцию и нашёл реальные исправленные ошибки:
Host/Origin при DNS rebinding, source-first MRI replay, сохранение контекста UI,
агрегация статуса reused failures, начальный пропуск индексного ряда, чрезмерное
перекрытие EMG-окон и квадратичный поиск fragmented MRI-компонентов.

Первый свежий TSS-прогон завершился таймаутом при shutdown и сохранён как failed.
Следующий прогон завершился нормально после адаптера: trusted parent main вернул 0,
MPS synchronize и runtime сохранены; собственный supervisor завершает дочерний процесс
через os._exit(0) только после успешного return, затем очищает процессную группу.
Parent script, TSS, веса и SpinoSarc не изменялись. Свежий результат содержит actual
SHA локальных весов, 15 observations / 27 artifacts; 5 CSA не оценены на sagittal
native plane. Это проверка одного открытого исследования, а не клиническая оценка.


## Итоговая приёмка

- Общий suite: **283 tests OK**, один optional imaging test skipped в system Python.
  MRI suite в существующем imaging-окружении: 20/20, без skips.
- Production registry не импортирует тяжёлые MRI-библиотеки в сервер.
- CLI `run-flow`: восемь модулей, partial из-за неизвестного осмотра, сохранён JSON.
- CLI `import-bundle`: настоящий SPIDER replay, 15 observations/partial, hashes проверены.
- Независимый HTTP smoke: 8 providers, 28 comparisons, 36 downloads SHA/size,
  clinical indeterminate → synthetic review met, один новый job, тот же MRI,
  прежний отчёт побайтно неизменен.
- После исправления EMG-region новый GUI flow: 26 unknown / 2 incompatible пары;
  это технические статусы, а не отрицательный диагноз.
- Новый live workflow через HTTP → Dispatcher → ProcessProvider → TSS:
  **91.90 s**, normal worker return, 10 assessed + 5 CSA not_assessed,
  actual step2 SHA `35fe49c6a8f6b700c9cbb2110985f4ecccc74b74fca630c9c37e5c653578f84c`.
  Идентификатор текущего реального примера и private audit: `var/latest-live-example.json`;
  clinical report: `var/latest-live-report.json`, unknown без осмотра.
- Safari: кнопки полного учебного запуска и отдельной оценки, изображения,
  численные значения, прямые цитаты и новый frozen report проверены через UI.

Запущенный пользовательский сервер: port 8766, store `var/multimodal`, local trusted
worker config `var/mri-worker.json`. Предыдущий CDS server на 8765 не изменялся.
Реальные файлы/веса/runtime не добавлены в Git; репозиторий локальный, без push.
