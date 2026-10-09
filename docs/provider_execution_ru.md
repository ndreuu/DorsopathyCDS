# Выполнение providers в фоновых потоках и отдельных процессах

`Dispatcher(repository, artifacts, registry, max_workers=4,
mri_module_ids=("mri.anatomy",), recover=True)` получает уже сохранённый job:
`enqueue(job_id)` возвращает `Future` сразу, пока provider продолжает анализ.
Повторная доставка незавершённого job возвращает тот же Future. Running/terminal
job не запускается; между dispatcher экземплярами единственный запуск защищён
SQLite compare-and-set `start_job`.

CPU jobs выполняются независимым пулом; очередь MRI имеет отдельный executor
с одним слотом и не занимает CPU-потоки. Ошибка одного provider не отменяет
другие задания. Идентификатор модели, исходные данные и ModuleResult 1.0 остаются
содержимым provider; dispatcher не интерпретирует медицинские признаки.

## Восстановление и завершение

Сервер должен иметь ровно одного владельца lifecycle dispatcher для своей базы.
При startup `recover=True` ранее running jobs получают сохранённый failed
результат с `worker_interrupted`; затем все queued доставляются заново.
Недоступная точная версия provider получает `provider_unavailable`. Новый запуск
после неудачи создаёт новый job ID. Завершённые results/snapshots не переписываются.
Для конкурентных тестовых dispatcher одной базы отключают recovery.

`close(wait=True)` запрещает новый enqueue, отменяет ещё не начавшиеся futures
(persisted jobs остаются queued), завершает все ProcessProvider process groups
и ждёт CPU threads. Уже выполняющуюся обычную Python-функцию нельзя безопасно
принудительно остановить: `wait=False` не ждёт её, но процессный worker всё равно
получает shutdown. HTTP/server должен вызвать close на выходе.

## Настоящий отдельный worker

```python
from pathlib import Path
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.process_provider import ProcessProvider

provider = ProcessProvider(
    ProviderSpec("mri.anatomy", "0.1.0", "MRI anatomy"),
    ("/trusted/mri-env/bin/python", "-m", "dorsopathy_workers.mri"),
    Path("var/workers"), timeout_seconds=1200,
)
registry.register(provider)
```

Команда берётся только из доверенной серверной конфигурации. Worker module должен
быть установлен в отдельном interpreter/environment; его imports не загружаются
при импорте платформы. Альтернатива — абсолютный путь к worker script.
Запуск получает один аргумент `--exchange-dir /private/job-workspace`.
Рабочая директория — этот workspace. Shell не используется; любые command/shell
поля request parameters не меняют argv. Тяжёлые библиотеки не входят в платформу.

Каждый job получает private каталог mode 0700, проверенные streamed копии входов
в `inputs/<sha256>` и bounded `request.json` (не более 8 MiB):

```json
{"schema_version":"1.0","request":"ModuleRequest object",
 "inputs":[{"artifact_id":"artifact-ID","relative_path":"inputs/SHA256"}]}
```

Worker использует `read_worker_request(exchange_dir)` из `process_io.py`; функция
возвращает envelope и проверяет входные SHA/size. Worker сохраняет производные
файлы внутри workspace и вызывает `write_worker_output(exchange_dir,result,derived)`:

```json
{"schema_version":"1.0","result":"ModuleResult 1.0 object",
 "derived":[{"artifact_id":"artifact-SHA256","relative_path":"overlay.png",
 "sha256":"SHA256","size_bytes":123,"media_type":"image/png","role":"derived"}]}
```

Это схема примера, строки-заглушки не являются валидной fixture. Result ArtifactRef
использует canonical `artifact-<sha256>` / `artifacts/<sha256>`. Каждый новый
artifact обязан иметь matching descriptor. Source refs остаются исходными.
Helper проверяет контекст/metadata/файлы и публикует output.json атомарно.
Adapter проверяет весь manifest до импорта; path traversal, symlinks, FIFO,
undeclared artifact, иной job/episode, неверные SHA/size и неоднозначный JSON
отклоняются. Затем add_file повторно проверяет содержимое; только проверенный
результат сохраняется. Неуспешный импорт может оставить недоступный orphan blob,
но не assessed observation. Временный workspace удаляется после исполнения.

stdout/stderr направлены в DEVNULL и не являются API результата. Timeout,
exit failure, startup failure, shutdown и malformed result дают явный failed
без текста данных пациента. Даже error/warnings, возвращённые failed worker,
заменяются безопасным статическим worker_reported_failure. POSIX process group завершается даже после нормального
выхода лидера; умышленно отделившийся от группы процесс этим механизмом не
изолируется. Workers — доверенный серверный код, а не security sandbox.
Windows process tree cleanup пока не реализован.

## Проверка без моделей

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_platform_process_provider.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_platform_dispatcher.py' -v
```

Fixtures в `tests/fixtures/process_workers/` запускают настоящий subprocess,
но производят только synthetic_demo. Они не регистрируются в приложении.
Проверки охватывают derived import, async/dedup, CPU parallelism, MRI isolation,
recovery, повреждённый manifest/файл, timeout и дочерние процессы. Это приёмка
исполнения программы, а не MRI-модели или клинических правил.
