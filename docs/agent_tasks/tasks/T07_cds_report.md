# T07 — Snapshot → проверяемый клинический отчёт

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen EpisodeReport/clinical intake T00.

## Владение

Новые `src/dorsopathy_cds/snapshot_adapter.py`, `episode_report.py`,
`tests/test_snapshot_adapter.py`, `tests/test_episode_report.py`,
`examples/snapshot_adapter/`, `docs/snapshot_adapter_ru.md`.
Не меняй engine.py, service.py, существующий каталог правил/цитат,
платформенный storage, providers, HTTP или UI.

## Работа

1. Реализуй frozen build_case(snapshot, context/selection) и evaluate_snapshot.
   Только выбранные результаты неизменяемого snapshot; не «latest эпизода».
   Исходные клинические оценки читаются через проверенный read-only artifact port.
2. В CDS-case допускаются только четыре FACT_SCHEMA и явно adult population.
   clinician_entered/synthetic_demo с assessment_ref и полной external lineage.
3. Patient_report, MRI-area, bulging, muscle asymmetry и model predictions
   не устанавливают неврологический синдром или clinical concordance.
4. Отсутствующая/неоценённая/непригодная клиническая оценка становится null
   с причиной. not_applicable обрабатывается согласно status исследования.
   Конфликт оценок — явный selection или indeterminate, не выбор по времени.
5. Проверяй linked data policy: чужая/синтетическая история рядом с публичным
   MRI не позволяет сделать подтверждённый клинический вывод для этого пациента.
   Клинические факты в таком case остаются null/неприемлемыми по frozen policy,
   а не met с предупреждением. Клиентский linked=true не отменяет origin mismatch.
   Concordance учитывается только для reviewed_result_id, выбранного в snapshot.
6. Вызови существующий evaluate_case. Оберни вывод в EpisodeReport: snapshot ID/hash,
   выбранные result/observation/evidence, версии, catalogue hash, mode, warnings.
   JSON и HTML используют одно содержание; HTML экранирует пользовательский текст.
   Сохрани manifest/selection и использованные правила/цитаты либо полную
   неизменяемую их копию по хешу. Старый HTML/export не вызывает текущий load_catalog.

## Приёмка

- Тесты: met/not_met/indeterminate, not_performed, неизвестное/отрицательное
  соответствие, отсутствующий осмотр, conflicting selections и unlinked data.
- Каждый известный clinical fact имеет проверяемую lineage к исходной оценке.
- Direct quotes и источники остаются из существующего каталога, без переписывания.
- not_met не объявляется исключением болезни; нет придуманных процентов.
- Одинаковый frozen input воспроизводит отчёт; новые results его не меняют.
- Обновление catalogue/selection не меняет прежний JSON/HTML; новая проверка
  создаёт новый report ID. Mismatched reviewed_result_id проверен тестом.

Сдай коммит, примеры полного/неполного отчёта и команды meaningful tests.
