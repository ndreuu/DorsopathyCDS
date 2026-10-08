# T04 — Жалобы и явные врачебные оценки

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen input/concepts T00.

## Владение

`src/dorsopathy_modules/complaints/`, `src/dorsopathy_modules/clinical_exam/`,
`examples/complaints/`, `examples/clinical_exam/`,
`tests/test_complaints_provider.py`, `tests/test_clinical_exam_provider.py`,
`docs/intake_modules_ru.md`. UI, API, CDS и общие schema не редактировать.

## Работа

1. ComplaintsProvider: intensity 0–10, область/сторона, длительность,
   выбранные зоны карты и время. Observation.value только scalar;
   полигоны/массивы карты тела сохраняй отдельным JSON artifact.
2. ClinicalExamProvider: четыре существующих CDS-факта, источник/время/assessment
   ID и явная ссылка на MRI-результат, который оценивал врач при concordance.
   Это ввод готовой оценки врача, не новая медицинская классификация.
3. У каждого assessed observation evidence на исходный ответ/оценку.
   Синтетические fixtures сохраняют synthetic_demo; настоящий ввод пациента
   и врача различается по origin. Оценку врача не выводи из жалоб пациента.
4. Валидация типов и противоречивых статусов, partial при неоценённых полях.
   Не исправляй недопустимые значения молча; не связывай внешнюю зону боли
   с уровнем позвоночника или корешком по одной стороне.

## Приёмка

- intensity 0 остаётся известным нулём; неизвестный ответ — not_assessed/null.
- «Осмотр не проведён» не превращается в отрицательный клинический тест.
- not_performed вводится явно. Для него concordance not_applicable/null;
  доступный MRI с неизвестным соответствием — not_assessed/null.
- Все четыре named facts соответствуют существующему FACT_SCHEMA.
- Concordance ссылается на точный reviewed_result_id. Для выполняющегося MRI
  остаётся неизвестным; после просмотра создаётся новая оценка, не перезапись старой.
- Автор/assessment_ref живут в source artifact, не в новом поле Observation.
- MRI публичного пациента плюс синтетическая анкета не становятся связанным
  клиническим случаем; это отражается в lineage/mode данных.
- Тесты сохраняют false/0/unknown и проверяют evidence/экспорт карты.

Сдай Provider-классы, синтетические входы/выходы, команды тестов и коммит.
