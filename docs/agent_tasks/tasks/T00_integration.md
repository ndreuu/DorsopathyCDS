# T00 — Интегратор: freeze и итоговая сборка

Ты реализуешь задачу в `/Users/andrew/Projects/DorsopathyCDS`.
Прочитай `AGENTS.md` и `docs/agent_tasks/README.md`. Начни из актуальной `main`
с taskpack; архитектурная база исходного каркаса — `bd5cc16`.

## Владение

`docs/e2e_contracts_ru.md`, `src/dorsopathy_platform/api_contracts.py`,
`src/dorsopathy_platform/schemas/api/v1/`, `examples/e2e_contracts/`, `tests/test_api_contracts.py`,
`src/dorsopathy_platform/bootstrap.py`, существующие общие contracts/ports,
registry/CLI, `pyproject.toml`, README и `docs/run_full_flow_ru.md`.
Существующие storage/orchestration делегированы T01/T02; не редактировать
их одновременно с владельцами. Не реализовывать чужие провайдеры.

## Работа

1. Прочитай все карточки и текущую платформу. Зафиксируй минимальные
   CaseManifest/WorkflowRun/EpisodeReport, worker exchange и API fixtures.
   Сохрани ModuleResult 1.0; дополнительные контракты должны быть отдельными.
   Включи T11–T16: sensor input profiles, calibration/time/clock/pose/frame,
   acquisition/processing modes, ComparisonReport и отдельные data/device gates.
2. Согласуй interface signatures storage/upload/dispatcher/application,
   функцию импорта MRI-экспорта и CDS `build_case/evaluate_snapshot`.
   Опиши селекторы PNG/кадра/контуров/измерений и внутренний клинический ввод.
   Зафиксируй исполняемую linked-policy, reviewed_result_id и второй этап
   клинической оценки с reuse точного MRI-результата. Freeze отчёта включает
   manifest/selection, адаптер и использованную редакцию каталога/цитат.
3. Сделай freeze-commit. Передай его хеш исполнителям. Подготовь отдельную
   ветку/worktree на задачу, один runtime store на worktree; опубликуй владение.
4. Собирай сдачи последовательными merge/cherry-pick. Регистрацию providers
   делай в bootstrap; MRI-импорты тяжёлых библиотек не должны появиться в API.
5. Подключи CLI `serve` и `run-flow`, конфигурацию live worker и modes.
   Изменения общего интерфейса после freeze согласуй с потребителями.
6. Вместе с T09 выполни полный synthetic/replay/live путь, оформи инструкцию
   и точный список поддерживаемых признаков. Заверши сборку отдельным коммитом.
   Прими MRI-flow первым этапом, затем все sensor modules/ComparisonReport.
   Не подменяй требование отдельных модулей одним MRI-демо.

## Приёмка

- После freeze каждый агент может проверить своё решение без готовности чужого.
- Точные маршруты/сигнатуры/JSON не расходятся между API, UI и workflow.
- Registry не содержит скрытых fake вместо настоящего провайдера.
- Чистый checkout запускает synthetic flow без MRI-зависимостей; live использует
  явно настроенное отдельное окружение, без изменения исходных MRI-репозиториев.
- Совместные проверки проходят; старое CDS-приложение работает.

Не жди реализации всех задач для сдачи freeze. После freeze продолжай интеграцию.
Результат: хеш freeze, таблица ownership/worktrees, команды запуска, финальный
коммит и результаты приёмки T09.
