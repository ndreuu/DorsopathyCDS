# Артефакты, загрузки и сохранённые workflow

T01 использует только стандартную библиотеку Python и SQLite. В Git находятся
синтетические тесты; `platform.sqlite3`, бинарные файлы и временные загрузки
живут в отдельном runtime-каталоге, выбранном доверенной конфигурацией.

## Каталог и скачивание

`ArtifactStore.add_bytes/add_file/open/read` сохраняют прежний интерфейс.
Хранилище публикует содержимое атомарно под `artifacts/<sha256>` и проверяет
размер, SHA и тип файла перед выдачей открытого бинарного потока. `add_file`
копирует блоками по 1 MiB. Symlink конечного файла и каталога `artifacts`
отвергается; путь из клиентского запроса не используется.

`SqliteRepository.register_artifact(episode_id, ref)` добавляет серверную
связь эпизода и доверенной ссылки, полученной из ArtifactStore.
`get_artifact(episode_id, artifact_id)` и `list_artifacts(episode_id)` возвращают
точную сохранённую metadata, проверяя её hash. Метод регистрации сам не читает
blob: импортёр обязан сначала использовать ArtifactStore, а public API принимает
только ID. Одинаковые байты дедуплицируются на диске; разрешённая связь каждого
эпизода независима. Повторная регистрация с иной metadata в том же эпизоде даёт
`ConflictError`. Чужой ID не разрешается, даже если его SHA известен.

## Потоковая загрузка одного файла

```python
from pathlib import Path
from dorsopathy_platform.storage import ArtifactStore, SqliteRepository
from dorsopathy_platform.uploads import UploadService

root = Path("/private/tmp/dorsopathy-example-runtime")
repository = SqliteRepository(root)
artifacts = ArtifactStore(root)
uploads = UploadService(repository, artifacts, max_bytes=256 * 1024 * 1024)
repository.create_episode("example-episode")
with Path("selected-synthetic.bin").open("rb") as source:
    ref = uploads.upload("example-episode", source, "application/octet-stream")
saved_ref, stream = uploads.open("example-episode", ref["artifact_id"])
with stream:
    first_block = stream.read(64 * 1024)
```

`UploadService.upload(episode_id, stream, media_type, declared_size=None,
role="source")` читает блоками не более 64 KiB в приватный временный файл
`uploads/.upload-<random>.pending`, затем вызывает `add_file` и регистрирует
связь. ZIP не распаковывается. Лимит по умолчанию — 512 MiB, его можно изменить
конфигурацией. Проверяются заявленный и фактический размер; превышение, лишние
байты, преждевременный EOF и ошибка чтения не публикуют catalog link.
Временный файл удаляется при успехе и ошибке. Отдельный blob без catalog link
может остаться после ошибки регистрации; скачать его через service нельзя.

Входной stream принадлежит вызывающему и не закрывается service.
`open(episode_id, artifact_id)` возвращает `(ref, verified_binary_stream)`;
скачивающий закрывает stream сам. Произвольные ArtifactRef/paths от клиента не
принимаются. MIME ограничен 255 ASCII-символами и не допускает control characters.
`UploadError.code == "upload_too_large"` означает configured size rejection;
остальные ошибки имеют `invalid_upload`. Текст ошибки не включает данные,
идентификаторы и локальные пути.

## Атомарный запуск и финализация

`create_run(episode_id, idempotency_key, manifest, job_requests=None,
run_id=None)` сохраняет run и его queued jobs одним `BEGIN IMMEDIATE`.
Запросы должны точно соответствовать module/version/input IDs/parameters/protocol
в manifest; input refs должны совпадать с каталогом эпизода. Пустые requests
разрешены для manifest без новых modules, с явно выбранными reuse results.
Reuse result проверяется в том же эпизоде. Связи новых jobs после создания run
не меняются.

Ключ уникален внутри эпизода. Тот же ключ и canonical manifest возвращают
сохранённый run, включая при повторной попытке с новыми случайными IDs jobs.
Иной manifest даёт `ConflictError` без создания второго run или части jobs.
Manifest и список jobs сохраняются с hash и читаются без пересборки.

`finalize_run(run_id, snapshot_id, report_ref, status)` публикует единственные
refs посредством compare-and-set из `running`. Snapshot и зарегистрированный
report должны принадлежать эпизоду; report имеет role `derived`.
Конкурирующий caller получает сохранённые победившие status/refs. Content-addressed
report blobs проигравших callers допустимы; законченные runs не переписываются.
`get_run/list_pending_runs` читают сохранённую metadata. `list_jobs(episode_id=None,
statuses=None)` возвращает jobs по ID; recovery dispatcher использует queued/running.
Storage сам не вычисляет клинический отчёт и не перезапускает worker.

## Миграция и проверки

Версия БД — `PRAGMA user_version=1`. При открытии старой БД добавляются только
`artifact_catalog`, `workflow_runs` и индексы в одной транзакции. Старые episodes,
jobs, results и snapshots остаются побайтно неизменными. Миграция повторяема;
ошибка откатывает новую schema, версия из будущего отвергается без удаления БД.

Проверки охватывают одновременные starts/finalization, rollback частично
создаваемых jobs, legacy migration failure/preservation, потоковый upload/download
файла больше 64 KiB, file-backed input, interruption, scopes, metadata/content
tampering и symlinks. Это проверки программного поведения, а не клиническая
валидация.
