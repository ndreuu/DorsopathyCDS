# T01 — Каталог артефактов и потоковая загрузка

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
`docs/agent_tasks/README.md` и frozen `docs/e2e_contracts_ru.md` из T00.

## Владение

`src/dorsopathy_platform/storage.py`, новый `uploads.py`,
`tests/test_platform_storage.py`, новые `test_platform_storage_migrations.py`,
`test_platform_uploads.py`, `docs/artifact_storage_ru.md`.
API/dispatcher/workflow и общие contracts не редактировать.

## Работа

1. Добавь серверный каталог ArtifactRef с привязкой к эпизоду: register,
   lookup по opaque artifact ID и episode scope, перечисление заданий.
   Hash и metadata хранятся сервером; клиент не задаёт путь/storage_key.
2. Реализуй frozen persistence для workflow metadata/ссылки на snapshot
   и report. Новые таблицы/поля мигрируются без потери существующих данных.
   Не меняй завершённые ModuleResult и snapshots.
   Idempotency key + canonical manifest hash атомарны; mismatch — conflict.
   Финализация run с snapshot/report refs — CAS, competing callers возвращают
   единственные сохранённые ссылки. Предоставь queries pending jobs/runs для recovery.
3. Сделай upload service для одного бинарного файла: поток во временный
   файл → add_file → регистрация. Не распаковывай ZIP в HTTP-слое.
4. Размер ограничивается конфигурацией; проверь declared/фактический размер,
   оборванный поток, cleanup и отсутствие частично зарегистрированных файлов.
5. Предоставь verified binary stream для download зарегистрированного ID.
   Старые add_bytes/add_file/read/open остаются совместимыми.

## Приёмка

- Файл больше 64 KiB проходит upload/download с тем же SHA; большого буфера нет.
- Чужой episode/artifact ID, подмена hash/size, traversal и symlink отклоняются.
- Дедупликация содержимого сохраняет отдельные разрешённые связи эпизодов.
- Прерванный upload не становится доступным для задания.
- База старой версии открывается после миграции; повторный запуск не теряет
  связи, jobs, snapshot и report. Ошибка миграции не стирает базу.
- Конкурентные операции и старые storage-тесты проходят.
- Конкурентные starts/finalization создают один run и один зафиксированный
  report; одинаковый key с разными manifests не смешивает случаи.

Начинай после freeze. Если HTTP ещё нет, проверяй upload на BytesIO и
файловом потоке. Сдай коммит, контрактные примеры и результат проверок.
