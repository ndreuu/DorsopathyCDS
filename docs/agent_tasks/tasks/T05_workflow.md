# T05 — Workflow одного эпизода и application service

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen CaseManifest/WorkflowRun/application T00.

## Владение

Новые `src/dorsopathy_platform/workflow.py`, `application.py`,
`tests/test_platform_workflow.py`, `tests/test_platform_application.py`,
`examples/workflow/`, `docs/episode_workflow_ru.md`.
Не редактируй storage, dispatcher, HTTP, provider-пакеты или clinical engine.

## Работа

1. Сохраняй выбранный manifest, versions, mode и IDs всех jobs до запуска.
   Описание run и polling воспроизводятся после перезапуска, а не из памяти UI.
   Canonical manifest hash + idempotency key используются через atomic storage API.
2. Запусти independent complaints/clinical intake/MRI-анатомию через dispatcher.
   Нет файла MRI — явная незапущенная ветвь, а не отрицательный результат.
   Поддержи выбранные LiDAR/thermal/EMG/US/nociception modules по manifest.
   Отсутствующий sensor source — not_connected/missing_input, не нулевое измерение.
3. Завершение всех обязательных ветвей даёт terminal workflow; failed branch
   не стирает результаты остальных. Опиши правила partial и skipped dependencies.
4. Опциональный disc_features запускается только при registered version и
   пригодных ROI; отсутствие детектора не создаёт норму. В первой сборке T10
   может отсутствовать. Данные передаются artifacts, не вызовами provider→provider.
5. Выбери точные result IDs, создай snapshot, вызови T07 и сохрани immutable
   report/ref. Polling готового run возвращает тот же snapshot/report.
   В мультимодальной сборке вызови T16 по frozen snapshot/protocol и сохрани
   ComparisonReport вместе с clinical report. Sensor observations не clinical booleans.
   После review врачом создай новый run/snapshot, явно reuse того же MRI result
   и новую clinical assessment. Manifest/selection и редакция каталога сохраняются.
6. Предоставь T06 frozen Application facade: episodes, upload, modules, start/get
   workflow, jobs/results/snapshots/report и проверенный доступ к artifacts.
   Application использует сервисы T01/T02, не запускает HTTP или GUI.
7. После startup T02 восстанови pending workflow по сохранённым job IDs.
   Сбой между completion jobs и freeze/report не должен оставить вечное running.
   Finalize через CAS T01; concurrent polling возвращает единственные refs.

## Приёмка

- Дублированный start по idempotency key не порождает второй прогон.
- Иной manifest с тем же key даёт conflict/409, а не переиспользует чужой run.
- Restart после jobs completion восстанавливает финализацию; параллельный polling
  не создаёт несколько clinical reports или меняющийся snapshot.
- Неудача MRI даёт понятный неполный workflow и сохраняет intake.
- Failed/непригодная анатомия не запускает дисковый детектор.
- Snapshot не подтягивает более поздние jobs; новый запуск — новый snapshot.
- Конфликт оценок передаётся T07 с selection, не разрешается «последним значением».
- Unlinked клиника не подтверждает гипотезу для публичного MRI.
- Reviewed MRI result входит в выбранный snapshot. Чужая/старая ссылка не
  устанавливает concordance; изменение selection не меняет предыдущий отчёт.
- Тесты на doubles реально проверяют зависимости, freeze и terminal states;
  при интеграции заменяются настоящими providers/dispatcher без смены API.

Сдай коммит, application signatures, manifest и отчёт примерного synthetic run.
