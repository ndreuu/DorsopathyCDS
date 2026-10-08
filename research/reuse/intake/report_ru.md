# T04: повторное использование жалоб и клинического ввода

Проверено 09.10.2026, Europe/Istanbul. Только исследование; без установки, реализации и изменения ModuleResult 1.0.

**Рекомендация:** локальный React-стек JSON Forms + react-body-highlighter, отдельный ручной выбор стороны/области. LHC-Forms — альтернатива для медицинского обмена FHIR. Компоненты дают формы и графику; evidence, происхождение и MRI linkage остаются локальным адаптером.

## Три готовых решения

| Решение | Reuse, API и форматы | Ограничения |
|---|---|---|
| JSON Forms 3.8.0 | `@jsonforms/core`, `@jsonforms/react`, `@jsonforms/vanilla-renderers`; `JsonForms({schema,uischema,data,renderers,cells,onChange})`; callback `{data,errors}`. Browser: JSON Schema + UI schema → JSON ответов/AJV errors; server: исходный JSON artifact и повторная валидация. [API](https://jsonforms.io/docs/integrations/react/), [stable release](https://github.com/eclipsesource/jsonforms/releases/tag/v3.8.0). | Типы, диапазоны и условные поля готовы; статусы unknown/NA и клиническое содержание задаются нашей схемой. |
| react-body-highlighter | `import Model from 'react-body-highlighter'`; `type="anterior"/"posterior"`, `data=[{name,muscles,frequency}]`, `onClick({muscle,data})`. Готовые SVG-полигоны и lower-back. Выбранные зоны → отдельный JSON bodymap artifact. [README](https://github.com/giavinh79/react-body-highlighter), [реальный callback](https://github.com/giavinh79/react-body-highlighter/blob/master/src/component/Model.tsx). | Callback не возвращает сторону: нужен отдельный select. frequency — цвет, не NRS. Нет freehand drawing/дерматомов; это внешние зоны, не корешки. Repository package.json: 2.0.5, publication pin проверить при интеграции. |
| NLM LHC-Forms (`lforms`) | Локальные JS/CSS через script/link; `LForms.Util.addFormToPage(q,container,{fhirVersion:'R4'})`; `getFormFHIRData('QuestionnaireResponse','R4',container)`. Browser: Questionnaire JSON → QuestionnaireResponse JSON; server сохраняет source artifact, затем адаптирует scalar answers. [Docs](https://lhncbc.github.io/lforms/). | FHIR-сервер и платный API не обязательны. Документация предупреждает: npm bundles пока не работают через import/require. Поддержка SDC частичная; remote lookup/prepopulation исключить из offline-профиля. Карты тела нет. |

## Код, artwork, questionnaire, data: лицензии отдельно

JSON Forms — [MIT](https://github.com/eclipsesource/jsonforms/blob/master/LICENSE), без медицинского контента. Browser bodymap — [MIT](https://github.com/giavinh79/react-body-highlighter/blob/master/LICENSE). README называет источник полигонов react-native-body-highlighter; upstream также [MIT](https://github.com/HichamELBSI/react-native-body-highlighter/blob/main/LICENSE). Отдельной artwork-лицензии не найдено: распространение MIT на assets — вывод из repository license, не независимая проверка прав художника. Сохранить оба notices и сверить версию заимствованных полигонов.

LHC-Forms использует **собственные BSD-based условия**, а не Apache/MIT; retain notices, no endorsement, citation при публикации. LOINC/UCUM имеют отдельные terms. [LICENSE.md](https://github.com/LHNCBC/lforms/blob/master/LICENSE.md). Лицензия движка не лицензирует все demo questionnaires.

Готовые опросники не включать в минимальный стек без проверки версии, языка и digital rights:
- ODI copyrighted; clinical/academic license может быть бесплатной, commercial — отдельные условия. Русский перевод и electronic version получать у Mapi. [Дистрибьютор](https://eprovide.mapi-trust.org/instruments/oswestry-disability-index), [условия автора](https://www.mapi-trust.org/news-events/news/the-oswestry-disability-i).
- PROMIS: digital integration обычно требует HEAP; есть исключение для отдельных некоммерческих исследований. Все переводы требуют разрешения; бесплатная paper PDF не означает свободный digital/Russian use. [Официальные условия](https://www.healthmeasures.net/explore-measurement-systems/promis/obtain-administer-measures).
- BPI: questionnaire и body drawing copyrighted; русский перевод доступен у владельца. Scoring/manuals и artwork проверять отдельно от кода. [MD Anderson](https://www.mdanderson.org/research/departments-labs-institutes/departments-divisions/symptom-research/symptom-assessment-tools/brief-pain-inventory.html), [copyright формы](https://www3.mdanderson.org/depts/prg/bpisf.pdf).

## Что уже покрывает CDS и что остаётся адаптеру

Уже есть synthetic `DemoComplaintsProvider` (integer 0–10), ModuleResult 1.0 validation/evidence и ровно четыре FACT_SCHEMA в `src/dorsopathy_cds/service.py`:
`clinical.specific_cause_warning_signs_absent`,
`clinical.radiculopathy_signs_and_positive_tension_tests`,
`clinical.imaging_clinical_concordance`,
`imaging.study_status`.
Из формы врача вводятся готовые оценки этих фактов; ответы пациента их не определяют.

Минимальные поля: intensity, duration+unit/recall window, region, side, selected external regions, acquisition time, отдельные clinician assessments. Вход browser — JSON формы + карты, вход provider — source artifacts; API `Provider.analyze(request,context)`, `context.artifacts.read`, `add_bytes`, `validate_result(result,request)`. Нового HTTP endpoint этот обзор не предполагает.

Observation.value остаётся scalar; intensity unit `{score}`. Arrays/polygons, author, assessment_ref, form/version и reviewed_result_id находятся в JSON artifact; evidence selector указывает точный ответ. Сохраняются source/time/side/region; `lower_back` не равно `lumbar_spine` и не даёт уровня/корешка.

Patient answers → `patient_report`; явная врачебная оценка → `clinician_entered`; fixtures → `synthetic_demo`. Известные `0` и `false` — assessed, без truthiness conversion. Unknown → `not_assessed/null`, NA → `not_applicable/null`. Неосмотренный тест не становится false. Для MRI `not_performed` concordance NA; доступный непросмотренный или выполняющийся MRI — unknown. Concordance требует ручного просмотра точного завершённого reviewed_result_id; затем новая assessment/snapshot. Пустая отвеченная карта `[]` отличается от неизвестной `null`.

Открытая переносимая схема — FHIR R4 QuestionnaireResponse: linkId, typed answers, authored, source/author. Она остаётся source artifact, не заменяет ModuleResult. QR.status — lifecycle всей анкеты; unknown/NA поля требуют отдельных coded/status items. FHIR specification CC0; сторонние терминологии/instruments отдельно. [Schema](https://hl7.org/fhir/R4/questionnaireresponse.html), [license](https://hl7.org/fhir/R4/license.html).

## Корпус и gate

[SPIDER v4](https://zenodo.org/records/10159290) содержит MRI, masks, demographics/scanner metadata и radiological gradings; в опубликованном составе нет pain questionnaires/осмотра. CC BY 4.0 подтверждает [data paper](https://www.nature.com/articles/s41597-024-03090-w). [Mendeley composite v2](https://data.mendeley.com/datasets/k3b363f3vz/2), CC BY 4.0, даёт sagittal images/annotations/spinal geometry, а не клинические ответы.

[BACPAC requirements](https://heal.nih.gov/files/2020-07/BACPAC%20Dataset%20Requirements%20and%20Recommendations%20-%20Longitudinal%20Assessments.pdf) полезны как field inventory; права individual instruments отдельно. [HEAL notebook](https://healdata.org/dashboard/Public/notebooks/BACPAC_Synthetic_Data_Analysis.html) явно synthetic.

**Gate:** в проверенных источниках не найден подтверждённый доступный корпус lumbar MRI + индивидуальные жалобы + осмотр + linkage/time. Нужен permissioned linked export, dictionary, patient/visit key, assessments и MRI refs. Общий PROM-корпус или реальный MRI с выдуманной анкетой gate не закрывает; unlinked/unknown не допускает подтверждающий CDS-case. Новых правил, психогенной боли/симуляции или диагностических вероятностей не предлагается.
