# T09 — Независимая сквозная приёмка

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief, все карточки и frozen T00 contracts.

## Владение

`tests/test_full_episode_flow.py`, `tests/test_platform_e2e.py`,
`tests/fixtures/full_flow/`, `scripts/run_e2e_acceptance.py`,
`docs/full_flow_acceptance_ru.md`, manifests/checksums демонстрационных данных.
Не исправляй production-файлы чужих компонентов: дефект → владелец → retest.

## Работа

1. Подготовь сценарии сразу после freeze. CI — полностью synthetic,
   отдельный local replay/live smoke — известный открытый MRI из T03.
2. Сценарий клиента: новый episode → потоковый upload → intake → async workflow
   → polling → snapshot/report → download evidence → JSON/HTML export.
3. Проверь известные ожидаемые кадр/единицы/контуры и origin, не только HTTP 200.
   После нового job и перезапуска прежний report/snapshot/evidence совпадают.
   После обновления каталога/selection старый export совпадает побайтно.
4. Негативные сценарии: missing exam, not_performed, unknown/discordant MRI,
   conflict, unlinked clinical data, failed worker, timeout, tampering,
   foreign artifact ID, interrupted upload, numbering needs_confirmation.
   Добавь неверный reviewed_result_id и второй clinical-review run без повторного
   MRI-inference; новый report появляется, исходный остаётся неизменным.
   Проверь concurrent start/finalize, key mismatch и restart в промежутках
   queued→dispatch и jobs terminal→snapshot/report.
5. В live исключи cache-only успех: новый job/workspace, входной SHA, process
   metadata и свежий экспорт подтверждают реальный inference. Запиши используемые
   версии/модели/источник/лицензию. Нет окружения — live не считается пройденным.
6. Проверка viewer вместе с T08: понятные статусы, evidence, цитаты, экспорт.
   Проверь полный существующий unittest suite; отдели software checks от accuracy.
7. Отдельно прими T11–T16: calibrated synthetic полный flow, настоящий импорт
   доступных exports и аппаратный capture при наличии устройства. Проверяй
   unknown scale/radiometry/sample rate/US calibration/device version/clock/frame
   и независимые unlinked datasets. Для каждого режима passed/failed/not_run.

## Приёмка

- Все критерии полного прогона из общего brief покрыты сценарием и результатом.
- Fake MRI не проходит как live; синтетические оценки не становятся реальной клиникой.
- Отказ ветви не стирает пригодные результаты; old snapshot/report неизменны.
- Есть одна инструкция, которой можно повторить прогон после чистого checkout.
- Каждый провал имеет task owner и воспроизводимый минимальный пример.
- В отчёте нет пустого «всё работает»: показаны passed/failed/not_run режимы,
  точные команды и material limitations. T10 оценивается отдельно.

Сдай коммит acceptance-артефактов/инструкций; raw MRI, веса, local reports и
скриншоты реальных данных остаются вне Git.
