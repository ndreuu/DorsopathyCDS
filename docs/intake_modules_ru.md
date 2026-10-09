# Жалобы и явный осмотр: первый исполняемый профиль

`ComplaintsProvider` (`complaints`, `0.1.0`) и `ClinicalExamProvider`
(`clinical.exam`, `0.1.0`) принимают структурированный JSON и возвращают
ModuleResult 1.0. Движки форм и UI из исследования пока не устанавливаются.
Провайдеры не классифицируют боль и не ставят диагноз.

## Вход жалоб

Контрольный источник: [source_known_zero.json](../examples/complaints/source_known_zero.json).
Поля frozen-профиля `complaints.v1`: `schema_version`, `source_kind`, `mode`,
`origin`, `acquired_at`, `form_id`, `form_version`, `assessment_ref`,
`author={id,role:"patient"}`, `anatomy={region:"lower_back",side}`, `answers`,
`bodymap`. Лишние и отсутствующие поля отклоняются.

`answers.pain_intensity` — целое 0–10 с unit `{score}`;
`answers.duration` — конечное неотрицательное число дней, unit `d`.
Каждый ответ содержит `assessment_status`, `value`, `unit`.
Известный ноль остаётся `assessed/0`; `not_assessed` и `not_applicable`
требуют `value:null`. Никаких преобразований строк, true/false или единиц.
Неизвестные ответы дают `partial`.

`bodymap={assessment_status,selected_regions}` содержит уникальные внешние
коды зон (opaque ASCII IDs), например `lower_back`. Отвеченная пустая карта
`[]` отличается от неизвестной `null`. Карта экспортируется отдельным
derived JSON `complaints.bodymap.v1` с source artifact ID/selector, временем,
автором и происхождением; в Observation нет массивов и искусственного count.
Неизвестная карта тоже сохраняется и делает результат `partial`.
Сторона вводится явно: `left/right/bilateral/midline/unspecified`.
Ни зона, ни сторона не определяют корешок или уровень позвоночника.

`mode:patient_input` требует `origin:patient_report`;
`mode:synthetic_demo` требует `origin:synthetic_demo`. Время — timezone-aware
ISO8601 либо null. Автор, form IDs и assessment_ref остаются в источнике;
Observation сохраняет acquired_at/origin. Для каждого scalar evidence:
`{kind:"json",pointer:"/answers/pain_intensity"}` либо `/answers/duration`.

## Вход врача и ссылка на МРТ

[source_no_mri.json](../examples/clinical_exam/source_no_mri.json) показывает
false, неизвестный осмотр и явно не выполненное исследование.
[source_reviewed.json](../examples/clinical_exam/source_reviewed.json)
показывает готовую оценку врача, включая **false** concordance.

`clinical-exam.v1` содержит `schema_version`, `source_kind`, `mode`, `origin`,
`acquired_at`, `assessment_ref`, `author={id,role:"clinician"}`, `facts`.
`mode:clinician_input` требует `clinician_entered`; синтетика сохраняет
`synthetic_demo`. `facts` включает ровно четыре существующих concept:

- `clinical.specific_cause_warning_signs_absent` — bool;
- `clinical.radiculopathy_signs_and_positive_tension_tests` — bool;
- `clinical.imaging_clinical_concordance` — bool;
- `imaging.study_status` — `not_performed` / `performed_result_available`.

Это ввод уже выполненной врачебной оценки. Жалобы пациента не определяют
эти факты. Каждый fact — `{assessment_status,value}`; у concordance также
`reviewed_result_id`. Все известные факты требуют строгого типа, unknown/NA
дают null. Осмотр, который не проведён, остаётся `not_assessed`.

Первый input artifact — source form. Известная true/false concordance требует
`performed_result_available` и **второй input artifact**: standalone
ModuleResult `mri.anatomy/0.1.0`, `complete` или `partial`, этого же episode,
с точным `result_id` из source. Failed, другое episode/module/version или
другой ID отклоняются. Дополнительный evidence concordance указывает второй
artifact `/result_id`; первый evidence всегда `/facts/<exact concept>`.
Unknown concordance допускается без MRI artifact и не заявляет reviewed ID.
Выполняющееся/неизвестное исследование задаётся `not_assessed/null`.
Явное `not_performed` требует concordance `not_applicable/null`, без review
и без MRI artifact. Автор/assessment_ref/review ID не добавляются в Observation.

После просмотра создают **новый source, job/run и snapshot**, повторно
выбирая точный MRI result. Провайдеры не изменяют старые результаты.
MRI artifact подтверждает контекст и доступный результат, но не доказывает
сам факт человеческого просмотра. Различие происхождений сохраняется и
даёт `mixed_origin_review`; внешний реальный MRI с синтетическим осмотром
не становится связанным клиническим корпусом. Mode/linkage в manifest
контролируются workflow и report adapter, а не объявляются этим провайдером.

## Локальный прогон без UI и моделей

Оба профиля используют один bounded JSON (максимум 1 MiB), без parameters.
Для clinical допускается второй bounded JSON MRI result. Source формы имеют
`role:source`, media type `application/json`; MRI-result artifact может быть
source upload или derived. Все записи в примере синтетические.

```sh
PYTHONPATH=src python3 - <<'PY'
import json
from pathlib import Path
from dorsopathy_modules.clinical_exam import ClinicalExamProvider
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

store = ArtifactStore(Path('var/intake-example'))
files = ['source_reviewed.json', 'reviewed_mri_result.json']
refs = [store.add_bytes((Path('examples/clinical_exam') / name).read_bytes(),
                        'application/json') for name in files]
request = json.loads(Path('examples/clinical_exam/module_request_reviewed.json').read_text())
request['input_artifacts'] = refs
result = ClinicalExamProvider().analyze(request, ProviderContext(store))
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
```

Для complaints аналогично импортировать `ComplaintsProvider`, использовать
`examples/complaints/source_known_zero.json` и соответствующий
`module_request_known_zero.json`. Ожидаемые полные выходы сохранены рядом;
их identity/evidence определяются содержимым исходных файлов и job ID.
`reviewed_mri_result.json` — учебное измерение, не запуск TSS и не диагноз.

Проверка провайдеров и всей программы:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*provider.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Проверяются 0/false/null/NA, evidence, карта [], происхождение/время, запрет
coercion, точная MRI linkage и неизменность уже сохранённого snapshot после
нового review. Эти тесты подтверждают поведение программного ввода;
клиническую валидность оценки врача они не устанавливают.
