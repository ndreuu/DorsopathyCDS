# T06 — Платформенный HTTP

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen API/Application T00.

## Владение

Новый `src/dorsopathy_platform/http.py`, `tests/test_platform_http.py`,
`docs/platform_api_ru.md`. Существующий `dorsopathy_cds/http.py`, storage,
application, bootstrap/CLI и web assets не редактировать.

## Работа

1. Реализуй согласованные маршруты на loopback; stdlib HTTP достаточно.
   Управляющие операции — JSON, upload — поток одного бинарного файла.
2. Создать эпизод → upload → modules → start workflow → poll → snapshot/report
   → evidence download. Вызовы идут только через frozen Application facade.
3. Start возвращает 202 и ID, не блокирует HTTP потоком MRI-инференса.
   Управляющие payloads имеют отдельный малый лимит, upload — configurable large limit.
4. Проверяй тип/длину/неоднозначность JSON и Content-Length. Согласованные
   400/404/409/413/415/500 с безопасными сообщениями; payload не логируется.
5. Artifact разрешается сервером в scope эпизода. Никаких локальных paths,
   argv/commands или client-supplied storage_key. Download потоковый.
6. HTML viewer обслуживается как доверенный bundled UI; произвольный uploaded
   HTML не исполняется как страница приложения. Подключение assets делает T00.

## Приёмка

- На fake application API fixtures совпадают с frozen контрактами.
- На T05 клиент создаёт эпизод, загружает файл, видит progress и открывает
  сохранённый report/evidence. Ошибка одной ветви не становится HTTP success анализа.
- Большая MRI-загрузка не упирается в прежний CDS JSON-limit 64 KiB.
- Вторая загрузка/обрыв/чужой ID/ошибочный Content-Type проверены тестами.
- Сервер корректно останавливается; старый CDS HTTP остаётся работоспособен.

Сдай коммит, curl-сценарий полного API-прохода и результаты tests.
До T05 используй fake application только в tests, без production fallback.
