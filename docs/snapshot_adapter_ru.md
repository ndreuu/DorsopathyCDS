# Snapshot → условия правила → сохранённый отчёт

`dorsopathy_cds.snapshot_adapter.build_case(snapshot, manifest, artifacts)`
возвращает `(case, lineage, warnings)`. `evaluate_snapshot` в
`dorsopathy_cds.episode_report` проверяет существующее правило через
`evaluate_case` и сохраняет EpisodeReport. Новые клинические правила,
диагностические проценты и обучение в этих модулях не добавлены.

## Какие сведения становятся клиническими фактами

Используются только четыре существующих FACT_SCHEMA из выбранных результатов
замороженного snapshot. Проверяются hash snapshot, episode и выбранные
module/version либо точные `reuse_result_ids`. Более поздний результат эпизода
сам по себе не выбирается. Несколько оценок одного факта требуют явного
`clinical_selection`; включая известную и неизвестную оценки. ID выбора должен
однозначно указывать наблюдение именно этого факта.

Для известного факта требуется `clinical.exam/0.1.0`, origin
`clinician_entered` или `synthetic_demo`, `quality=usable`, source JSON
`clinical-exam.v1`, автор с role clinician и assessment_ref. Проверяются размер
и SHA файла, точный evidence selector `/facts/<concept>`, совпадение типа/значения,
assessment_status, origin и acquired_at с наблюдением. False сохраняется как
false; null остаётся неизвестным. Ответ пациента, размер канала и предсказание
модели не преобразуются в неврологический синдром или клиническое соответствие.

Известная concordance требует выбранного `mri.anatomy/0.1.0` результата
complete/partial того же эпизода. `reviewed_result_id` из исходной оценки
и отдельный evidence `/result_id` должны указывать на полный JSON точно этого
результата; совпадения только строки ID недостаточно. Failed, чужой, другой
или невыбранный результат оставляет concordance неизвестной. Программная
проверка записи не удостоверяет, что врач действительно осмотрел пациента.

Явное `not_performed` без выбранного MRI оставляет concordance not_applicable,
как предусмотрено существующим правилом. Если при этом выбран любой MRI-result,
получается противоречие: study status и concordance переводятся в unknown.
Отсутствующий файл никогда автоматически не означает `not_performed`.

## Связанность источников

`unlinked`/`unknown` не дают подтверждающий CDS-case: значения всех клинических
фактов остаются null с причинами в lineage. Смешение известных synthetic и real
origins блокируется даже при декларации `verified_linked`.
`synthetic_linked` допускается для учебного synthetic потока с synthetic origins.

Для реальных данных `verified_linked` требует доступного bounded JSON:

```json
{
  "schema_version": "1.0",
  "source_kind": "linkage-attestation.v1",
  "episode_id": "example-episode",
  "subject_ref": "corpus:subject-001",
  "session_ref": "session-001",
  "assessment_ref": "source-binding-001",
  "author": {"id": "operator-001", "role": "research_operator"},
  "sources": [{"artifact_id": "artifact-<sha256>", "sha256": "<sha256>"}]
}
```

Author role может быть clinician/research_operator. Каждый выбранный result
должен иметь исходный role=source artifact; все такие ссылки должны быть
перечислены с точными SHA. Известные subject/session противоречия в metadata
также блокируют связывание. Это проверка сохранённых bindings, а не независимая
идентификация человека или подтверждение квалификации автора.

Artifact reader предоставляет verified `open(ref)` и, для внешней аттестации,
`get_artifact(episode_id, artifact_id)` с проверкой scope каталога. Standalone
adapter сначала разрешает refs из самого snapshot. Недоступная аттестация или
неполные bindings дают unknown; произвольный путь от клиента не используется.

## Что сохраняется и как читать

Lineage каждого факта содержит выбранное observation/result, source ID/SHA,
selector, origin/time, автора, assessment_ref, reviewed result и причины отказа.
Отвергнутые и конфликтующие кандидаты остаются видимыми. Case содержит только
поля старого service; его ID — короткий hash snapshot и manifest, без персональных
данных. ModuleResult 1.0 и engine/service/catalog не изменены.

EpisodeReport сохраняет manifest/selection, adapter version, clinical case,
вывод существующего engine, полные `catalog_snapshot={rules,sources}` и его SHA,
lineage, warnings, технический ComparisonReport и snapshot ID. Report ID/SHA
детерминированы содержанием; меняющегося timestamp нет. При изменении каталога
в процессе вычисления публикация отвергается. Новый осмотр/выбор создаёт новый
отчёт. `render_html(report)` читает только сохранённое содержание, проверяет
hash и экранирует текст; обновление текущего каталога не меняет старый HTML.

`not_met` означает невыполнение условий формализованного правила, а не исключение
болезни. Прямые цитаты и версии источников сохраняются из текущего существующего
каталога без переписывания. Требование врачебной проверки остаётся в отчёте.

## Синтетические примеры и проверки

В `examples/snapshot_adapter/` сохранены complete/partial snapshots, manifests,
JSON/HTML отчёты и точные маленькие исходные blobs. Они не являются пациентскими
данными или свежим MRI inference.

```sh
PYTHONPATH=src python3 examples/snapshot_adapter/run_example.py --case complete
PYTHONPATH=src python3 examples/snapshot_adapter/run_example.py --case partial
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_snapshot_adapter.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_episode_report.py' -v
```

Проверяются три ветви CDS, false/null, NA, exact MRI review, выбор конфликтов,
missing/corrupt sources, unlinked/mixed origins, bindings/hash, сохранённые
цитаты и HTML после изменения каталога, escaping и детерминированность. Эти
проверки подтверждают программное поведение, а не клиническую валидность.
