# Frozen interfaces первой мультимодальной реализации

Дата: 9 октября 2026. ModuleRequest/ModuleResult 1.0 остаются без изменений.
Этот документ и `api_contracts.py` фиксируют отдельный workflow transport.
Исполнители работают от freeze-коммита в отдельных worktrees, меняют только
файлы своей карточки. Интегратор владеет bootstrap, CLI, общей сборкой и этим документом.

## Manifest, run, application

CaseManifest имеет ровно поля `schema_version="1.0"`, `episode_id`,
`population="adult"`, `mode=synthetic|replay|live`,
`linkage=synthetic_linked|verified_linked|unlinked|unknown`,
`linkage_evidence_artifact_id=null|ID`, `modules`, `reuse_result_ids`,
`clinical_selection={fact_concept:observation_id}`, `protocol_id=null|ID`.
Выбор модуля: `{module_id,module_version,input_artifact_ids,parameters}`.
Клиент передаёт IDs, а сервер получает полные ArtifactRef из каталога эпизода.
Сервер не принимает storage_key, filename/path или shell command от клиента.
Значение verified_linked требует отдельного source artifact-аттестации;
декларация не отменяет противоречие происхождения. В первом demo используем
synthetic_linked; реальный неподтверждённый корпус — unlinked/unknown.

WorkflowRun: `schema_version,run_id,episode_id,idempotency_key,manifest,
manifest_sha256,status,job_ids,snapshot_id,report_artifact`.
status — `running|complete|partial|failed`; последние два refs сначала null.
Jobs создаются атомарно с run до доставки dispatcher. Reuse results явно
выбраны в manifest, принадлежат этому эпизоду и не запускаются повторно.
Сохранённые result/snapshot/report не изменяются после нового осмотра.

Application(repository, artifacts, registry, dispatcher, report_builder):
`create_episode(episode_id=None)`, `upload(episode_id,stream,media_type,declared_size=None)`,
`modules()`, `start_run(manifest,idempotency_key)`, `get_run(run_id)`,
`get_job(job_id)`, `get_result(result_id)`, `get_snapshot(snapshot_id)`,
`get_report(run_id)`, `open_artifact(episode_id,artifact_id)`, `close()`.
get_run финализирует завершённые jobs через CAS; на startup pending runs также
проверяются. CPU-состав независим, один MRI inference-slot.

## T01 storage/upload API

Сохраняются старые методы ArtifactStore/SqliteRepository.
Добавляются `register_artifact(episode_id,ref)->ref`,
`get_artifact(episode_id,artifact_id)->ref`, `list_artifacts(episode_id)->list`,
`get_episode(episode_id)->dict`, `list_jobs(episode_id=None,statuses=None)->list`.
`create_run(episode_id,idempotency_key,manifest,job_requests=None,run_id=None)->run`
атомарно создаёт run и requests. Повторный ключ/тот же canonical manifest
возвращает существующий run; другой manifest — ConflictError(StorageError).
Jobs requests контекст совпадает с эпизодом; validate_request применяется.
`get_run(run_id)`, `list_pending_runs()`,
`finalize_run(run_id,snapshot_id,report_ref,status)->run`: CAS из running;
другой caller получает опубликованные победившие refs. Snapshot/report scope
проверяется; лишние непубликуемые content-addressed artifacts допустимы.
Необязательный update_run не нужен: IDs jobs фиксируются в create_run.
Recovery T02 использует list_jobs, start_job, complete_job (failed envelope).

UploadService(repository,artifacts,max_bytes=...):
`upload(episode_id,stream,media_type,declared_size=None,role="source")->ref`,
`open(episode_id,artifact_id)->(ref,verified_binary_stream)`.
Ограниченный буфер, declared/actual size, закрытие caller-owned input явно
документировать; orphan blob без catalog link не является успешной загрузкой.
Ошибки — UploadError(StorageError), без текста данных пациента.

## T02 execution/file exchange

Dispatcher(repository,artifacts,registry,max_workers=4,
mri_module_ids=("mri.anatomy",),recover=True): `enqueue(job_id)`,
`close(wait=True)`. CPU и single-slot MRI — отдельные executors. Дедупликация
локально и storage CAS; восстановление running как failed с кодом
worker_interrupted, затем dispatch queued. Unknown provider version — failed.

ProcessProvider(spec,command,workspace_root,timeout_seconds=...): existing
analyze; command — trusted argv tuple. К нему добавляется `--exchange-dir DIR`.
request.json: `{schema_version:"1.0",request:ModuleRequest,
inputs:[{artifact_id,relative_path}]}`; inputs копируются в `inputs/<sha>`.
worker пишет `output.json`: `{schema_version:"1.0",result:ModuleResult,
derived:[{artifact_id,relative_path,sha256,size_bytes,media_type,role}]}`.
Только role=derived; каждый derived соответствует объявленному result artifact;
ArtifactRef в worker использует canonical artifact-<sha>/artifacts/<sha>.
Импорт через add_file сохраняет этот ID и перепроверяет SHA/size. Источники
остаются original request refs. Relative paths только внутри private workspace,
без symlinks; bounded JSON. stdout/stderr не публичное API. Timeout/exit/JSON/
context mismatch явно failed; cleanup всей process group даже при exit лидера.

## T04 intake profiles

Complaints source: `schema_version:"1.0",source_kind:"complaints.v1",
mode:synthetic_demo|patient_input,origin:synthetic_demo|patient_report,
acquired_at:null|ISO,form_id,form_version,assessment_ref,
author:{id,role:"patient"},anatomy:{region:"lower_back",side},
answers:{pain_intensity:{assessment_status,value,unit:"{score}"},
duration:{assessment_status,value,unit:"d"}},
bodymap:{assessment_status,selected_regions:[]|null}`.
concepts symptoms.pain_intensity/{score}, symptoms.pain_duration/d;
body map только JSON artifact, без искусственного count concept.

Clinical source: `schema_version:"1.0",source_kind:"clinical-exam.v1",
mode:synthetic_demo|clinician_input,origin:synthetic_demo|clinician_entered,
acquired_at:null|ISO,assessment_ref,author:{id,role:"clinician"},facts`.
facts — ровно четыре FACT_IDS из api_contracts. Каждый `{assessment_status,value}`;
concordance дополнительно `reviewed_result_id:null|ID`.
Explicit not_performed требует concordance not_applicable/null; running/неизвестное
исследование — unknown. Known concordance требует performed_result_available
и вторым input artifact проверенный MRI ModuleResult этого эпизода с точным ID
и status complete/partial. failed не допускается. До обзора — unknown.
Поздний review создаёт новый source/run/snapshot с reuse точного MRI result.
Оценки врача нельзя получить из ответов пациента. Origin синтетики сохранён.

## MRI, sensors, evidence и comparison

Provider versions первого этапа 0.1.0. IDs: complaints, clinical.exam,
mri.anatomy, lidar.posture, thermography, emg.surface, ultrasound, nociception.
Дисковый classifier не регистрируется до обучения/приёмки T10.
Для приборов первый поддержанный профиль — калиброванные статические данные
или записанный экспорт; acquisition отдельно synthetic|recorded|device,
processing fresh|replay в JSON source/sidecar. device остаётся unsupported,
пока нет конкретного verified SDK/профиля. Synthetic inputs используют тот же
численный метод, но origin synthetic_demo. Unknown calibration/time/numbering
сохраняются; unsupported output не выдаётся как нормальное измерение.

Concepts: posture.landmark_distance/mm; posture.landmark_angle/deg;
thermal.roi_mean_temperature/Cel; thermal.left_right_delta/K;
emg.rms/uV (только подтверждённая амплитудная шкала);
emg.median_frequency/Hz; ultrasound.distance/mm; ultrasound.area/mm2;
nociception.device_index/1 (0–100 без нового расчёта proprietary ANI/NOL).
Профили источников выбирает владелец из research, публикует их в своём docs
и fixtures; метод измерения/единицы не являются диагностическим правилом.

MRI replay импортирует реальный export с геометрией и связанными кадрами.
Export bridge API: `import_export(export_path,context,request)->ModuleResult`;
source paths только доверенная offline config, HTTP загружает source bundle
без произвольных host paths. Worker live использует существующий parent
pipeline read-only; новый inference не подменяется replay.
PNG selector: `{kind:"image",frame_index,coordinate_frame}`;
измерение: `{kind:"measurement",measurement_id,frame_index}`;
JSON source: `{kind:"json",pointer:"/..."}`. Дополнительные контуры и endpoints
в measurement sidecar; mask_kind anatomy/ROI/pathology различается.

T16: `compare_snapshot(snapshot,manifest,artifacts)->dict` в собственном
`dorsopathy_platform/comparison.py` без orchestration/clinical engine.
ComparisonReport `{schema_version:"1.0",snapshot_id,pairs,warnings}`;
pair `{left_result_id,right_result_id,status:compatible|incompatible|unknown,
operations:[...],reasons:[...],evidence_refs:[...]}`. Время/зоны/frames проверяются
отдельно, не создаётся диагностический coherence процент.

## T07 reports и T06 HTTP

`build_case(snapshot,manifest,artifacts)->(case,lineage,warnings)`;
`evaluate_snapshot(snapshot,manifest,artifacts,comparison=None)->EpisodeReport`.
`render_html(report)->str` использует только frozen report content.
EpisodeReport имеет schema_version/report_id/snapshot_id/content_sha256,
manifest, selection, adapter_version, catalogue snapshot/hash, case,
clinical_report, lineage, warnings, comparison. ID детерминирован по содержанию;
нет generated_at, меняющего hash при конкурентном polling. Используются только
выбранные results, conflict без explicit selection -> unknown. Неопригодные
origins/linkage/review не превращаются в met. Прямые quotes из старого каталога.

Маршруты: GET /health, /modules; POST /episodes; POST /episodes/{id}/artifacts
(raw binary, Content-Type, Content-Length); GET /episodes/{id}/artifacts/{aid};
POST /episodes/{id}/runs (body CaseManifest, Idempotency-Key header);
GET /runs/{id}, /runs/{id}/report, /runs/{id}/report.html;
GET /jobs/{id}, /results/{id}, /snapshots/{id}; GET / — новый локальный viewer.
POST run -> 202; mismatch idempotency ->409; unknown IDs ->404; malformed ->400;
oversize ->413. Loopback только, нет shell/executable/path из HTTP. Тяжёлые
библиотеки не импортируются при serve. Старый dorsopathy_cds serve сохраняется.

GET /demo-inputs отдаёт только встроенные synthetic source profiles для учебного
сквозного запуска. Это не registry fake: эти входы обрабатывают реальные численные
методы зарегистрированных провайдеров. Учитывается source origin synthetic_demo.

В этой сборке root также владеет T05 workflow/application и T06/T08 HTTP/viewer;
их файлы не делегированы другим агентам. T01/T02/T04 и следующие аналитические
провайдеры имеют отдельных владельцев. Python dependencies для сенсоров optional;
обычный JSON/static synthetic flow запускается без torch/TSS окружения.
