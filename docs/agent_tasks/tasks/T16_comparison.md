# T16 — Сопоставление модальностей и карта пригодности данных

Работай в `/Users/andrew/Projects/DorsopathyCDS`, в своей ветке/worktree.
Прочитай AGENTS.md, общий brief и frozen ComparisonReport/input T00.

## Владение

Новый `src/dorsopathy_platform/comparison.py`, `tests/test_multimodal_comparison.py`,
`examples/multimodal_comparison/`, `docs/multimodal_comparison_ru.md`.
Не меняй workflow, viewer, providers, shared schema или clinical engine.

## Работа

1. По выбранному snapshot и protocol metadata построй ComparisonReport:
   доступность модулей, quality, subject/session/source linkage, область/сторона,
   временные окна, clock domains, pose и coordinate frames.
2. Каждая пара имеет compatible/incompatible/unknown для конкретного
   сравнения и причины/evidence. Определи требования к сравнению, не medical score.
   Один episode ID или client linked=true не доказывает связанность.
3. Поддержи выбор события/позы для субъективной боли, signal windows и внешних
   наблюдений, если исходные данные действительно содержат такую связь.
   Processing timestamp не заменяет acquisition; MRI может относиться к другой дате.
4. Пространственное совмещение — только по отдельному проверенному transform
   с frame IDs/units/method/error и source landmarks. Без него раздельные панели
   и семантические области с явным отсутствием точной регистрации.
5. Верни сохранённый sidecar report для T05/T07/T08: матрица сравнений,
   ссылки на карты/сигналы и причины невозможности сопоставления.
   Не вызывай providers и не заполняй существующие clinical booleans.

## Приёмка

- Synthetic aligned fixture, shifted times, incompatible frames, missing
  calibration и unlinked sources проверяют статусы и evidence.
- Отдельные открытые корпуса не объединяются в историю одного пациента.
- Наложение тепло/LiDAR/body map имеет проверенные frame transforms либо явно
  отсутствует. MRI→поверхность не становится точным от общего номера уровня.
- Нет искусственного процента согласованности, диагноза/воспаления/симуляции.
- ComparisonReport зафиксирован вместе с manifest/selection и snapshot/hash;
  поздние данные не меняют старый отчёт.

Стартуй на frozen fixtures параллельно T11–T15. Совместная приёмка использует
реальные outputs этих задач, а не собственные альтернативные schemas.
