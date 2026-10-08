# T02 — Фоновое выполнение и ProcessProvider

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen контракты T00.

## Владение

`src/dorsopathy_platform/orchestration.py`, новые `dispatcher.py`,
`process_provider.py`, `process_io.py`, `tests/test_platform_orchestration.py`,
новые `test_platform_dispatcher.py`, `test_platform_process_provider.py`,
`tests/fixtures/process_workers/`, `docs/provider_execution_ru.md`.
Не меняй storage, HTTP, MRI-worker, общие ports/schema или clinical engine.

## Работа

1. Долгоживущий dispatcher принимает persisted queued job и возвращает
   управление сразу. CPU-задания независимы; для тяжёлого MRI — один слот.
   Двойная доставка не запускает один job повторно.
2. ProcessProvider реализует existing ProviderProtocol и frozen file exchange.
   Команда и interpreter берутся только из доверенной конфигурации сервера.
   HTTP/parameters не позволяют передать shell-команду или произвольный executable.
3. Отдельный workspace на job, request JSON, output manifest и производные файлы.
   Проверь контекст результата, SHA/size, допустимые пути/ссылки и импорт derived
   artifacts перед completion. Не доверяй stdout как единственному результату.
4. Тайм-аут, exit≠0, испорченный JSON и неверный job приводят к явному failed.
   Заверши worker и дочерние процессы; не сохраняй stderr/пациентский текст
   в публичной ошибке. Shutdown не оставляет бесхозный inference.
5. На старте найди прежние running jobs: зарегистрируй явное прерывание по
   frozen storage API. Повторная попытка имеет новый job ID; успех не выдумывается.
   Затем доставь сохранённые queued, включая сбой до передачи dispatcher.
   Недоступная сохранённая версия provider получает явный отказ, не вечное queued.

## Приёмка

- Submit/HTTP 202 не ждут конца анализа; polling видит реальные статусы.
- Независимые CPU jobs выполняются параллельно, MRI-slot сериализуется.
- Тестовые workers проверяют success, timeout, child cleanup, malformed output,
  wrong context и попытку сослаться на файл вне workspace.
- Повреждённый артефакт не превращается в assessed observation.
- Import платформы/serve не загружает torch/TSS/MuscleMap.
- Перезапуск явно показывает прерванную работу; старые snapshots не меняются.
- Saved queued после restart реально исполняются, повторная доставка не дублирует job.

До готовности T03 используй отдельные явно тестовые workers. Сдай коммит,
команду проверки worker-double и инструкцию подключения настоящего worker.
