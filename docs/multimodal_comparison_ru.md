# Техническая пригодность сопоставления модулей

`dorsopathy_platform.comparison.compare_snapshot(snapshot, manifest, artifacts)`
не вызывает модели и не создаёт клинических фактов. Он возвращает frozen
ComparisonReport с snapshot ID, парами результатов, тремя операциями и evidence.
`compatible` относится только к требованиям указанной операции. Процент
согласованности боли, диагноз, воспаление и исключение заболевания не вычисляются.

## Metadata отдельным JSON sidecar

Measurement/source JSON может иметь `comparison_metadata`:

```json
{
  "schema_version": "1.0",
  "subject_ref": "corpus:subject-001",
  "session_ref": "session-001",
  "protocol_id": "protocol-001",
  "linkage_evidence_refs": [],
  "acquired_interval": {"start": null, "end": null, "clock_domain": null},
  "event_id": null,
  "pose_id": null,
  "calibration_status": "unknown",
  "frame": {"id": null, "units": null, "axes": null},
  "transform_refs": [],
  "calibration_evidence_refs": [],
  "clock_evidence_refs": []
}
```

Последние два поля optional. Evidence имеет стандартную форму
`{artifact_id,selector:{kind:"json",pointer:"/..."}}`; refs разрешаются
через выбранный snapshot или scoped artifact reader. Отсутствующие/повреждённые
metadata и несколько различных редакций одного result дают unknown. Исходные
clinical forms сохраняют прежнюю строгую схему, metadata живёт отдельно.
ModuleResult 1.0 не расширяется.

Subject ref включает namespace корпуса. Один episode ID, одинаковый номер
позвонка или marker linked не доказывают связь пациентов. Сначала выполняется
та же source-binding policy, что в CDS adapter; известное смешение origins или
разные subject refs несовместимы. Несвязанные открытые корпуса остаются разными
историями. Real attestation проверяет bindings/известные противоречия, не
удостоверяет личность автора или пациента.

## Три операции

1. `semantic_region`: доступные usable anatomy, грубая область и сторона.
   lumbar/lumbar_spine/lower_back объединены только как грубая поясничная область.
   Это не точная регистрация, не дерматом и не причина боли. Неизвестная сторона
   остаётся unknown; явно разные области/стороны несовместимы.
2. `event_window`: timezone-aware acquisition start/end, общий clock domain,
   пересечение окон, точный event ID и pose ID. Разные clock domains требуют
   внешнего mapping; он в первой версии не реализован. Real clocks дополнительно
   требуют доступных clock evidence refs. Synthetic common clock демонстрирует
   программную проверку. Processing time не заменяет acquisition; раздельные
   даты MRI/сенсора не превращаются в синхронное измерение.
3. `spatial_overlay`: метрические 3D frames, pose, calibration с доступными
   численными source refs и общий подтверждённый frame либо отдельный transform.
   Marker verified без calibration evidence недостаточен. Общий real frame
   также требует общего calibration artifact. Pixel/2D↔3D projection не реализован;
   тепловизионный JPEG и LiDAR остаются отдельными панелями без camera calibration.

Каждая operation содержит kind/status/reasons/evidence_refs; event может иметь
сохранённое пересечение `window`, пространственная операция — `transform_ref`.
Общий pair status: incompatible если хотя бы одна операция несовместима,
unknown если есть неизвестная, иначе compatible. Читайте отдельные operations:
успех грубого анатомического сравнения не подтверждает наложение изображений.
Failed result или недоступный первичный evidence оставляет операции unknown,
другие пары продолжают анализироваться.

## Transform — только проверка prerequisites

Transform refs указывают на JSON object `coordinate-transform.v1` с
`schema_version`, `from_frame`, `to_frame`, `units:"mm"`,
`mapping_direction:"from_to"`, конечной невырожденной affine 4×4 `matrix`,
`method`, `version`, `pose_id`, `validation`. Validation имеет
`status:"verified"`, `error_value`, `error_unit:"mm"`, `max_error`,
`landmark_evidence_refs` и `validation_evidence_refs`.

Проверяются оба frame IDs, направление, pose, units, матрица, численные
landmark refs и измеренный residual (scalar или `error_value`/`rms_mm`),
не превышающий declared error и max_error. Max_error — явно заявленная
техническая граница протокола, не клинический норматив. Transform сохраняет
своё from→to независимо от порядка result IDs пары. Проверка не выполняет
registration, inverse/resampling и не создаёт совмещённое изображение.
Даже compatible transform prerequisites не удостоверяют физическую точность
сканирования; метод и валидационные evidence проверяются отдельно при применении.

## Проверка

`examples/multimodal_comparison/` содержит небольшие связанные synthetic
aligned/shifted snapshots и точные JSON blobs. Это fixtures операций, не
предположение о наличии связанного реального мультимодального корпуса.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_multimodal_comparison.py' -v
```

Проверяются aligned/shifted windows, события/позы, unknown calibration/clock,
frame/projection gates, разные subjects, unlinked policy, referenced transform
и residual limit, unavailable evidence и поздний невыбранный result.
