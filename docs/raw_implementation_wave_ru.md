# Параллельная реализация raw-модулей

Дата запуска: 10 октября 2026. Интеграционная ветка: `feature/multimodal-mvp`.
База: `fb4904a`. Согласование выполнено до изменений провайдеров.
Интеграция и проверка: 11 октября 2026.

Общий `ModuleResult 1.0` сохраняется. Старые profiles/providers `0.1.0`
продолжают работать; сырой профиль имеет отдельную версию `0.2.0`.
Не добавлять host paths/commands в загрузки. Тяжёлые библиотеки запускаются
через `ProcessProvider` и доверенный worker config владельца платформы.
Общий bootstrap, CLI, UI, схемы и clinical catalog меняет только интегратор.

| Поток | Исполнитель | Собственные файлы | Согласованная граница |
|---|---|---|---|
| RAW-EMG | `/root/implement_raw_emg` | `emg_surface/`, `workers/emg/`, `test_emg_raw*`, `examples/emg_raw/`, `docs/emg_raw_ru.md`, собственный script/requirements | `emg.surface/0.2.0`, CSV/gzip + acquisition manifest; `emg.rms`/`emg.envelope` uV и `emg.median_frequency` Hz; физическая амплитуда неизвестна без масштаба |
| RAW-US | `/root/implement_raw_ultrasound` | `ultrasound_raw/`, `workers/ultrasound/`, `test_ultrasound_raw*`, `examples/ultrasound_raw/`, собственные doc/script/requirements | `ultrasound/0.2.0`, raw PNG/DICOM + acquisition manifest; target lumbar_multifidus; area mm2/thickness mm только при пригодной плоскости/калибровке и реальной маске модели |
| RAW-THERMAL | `/root/implement_raw_thermal` | `thermography_raw/`, `workers/thermography/`, `test_thermography_raw*`, `examples/thermography_raw/`, собственные doc/script/requirements | `thermography/0.2.0`, FLIR radiometric JPEG + acquisition manifest; автоматический gross foreground, mean/max Cel; анатомическая сторона/пара не выдумывается |
| RAW-3D | `/root/implement_raw_emg`, второй этап | Новый пакет `lidar_raw/`/worker и свои fixtures/tests/doc | `lidar.posture/0.2.0`, калиброванные depth/RGB/geometry; автоматические внешние ориентиры с явным методом, динамика только при последовательности |
| RAW-NOCI | `/root/implement_raw_thermal`, второй этап | Новый пакет `nociception_raw/` и свои fixtures/tests/doc | `nociception/0.2.0`, документированный экспорт конкретного прибора; time/index/QC/events, фирменный индекс не вычисляется заново |
| INTAKE + CDS | `/root` | Новые detailed intake packages, clinical adapters/catalog/report, bootstrap/CLI/UI и интеграционные tests/docs | Факты из исходных ответов/осмотра, цитаты/условия, трёхзначная логика; unsupported sensor→diagnosis mappings остаются явно недоступными |

Новый агрегированный `--raw-workers-config` содержит доверенные
`command`/`timeout_seconds` по выбранным raw-модулям; `--exchange-dir`
добавляет платформа. Провайдер регистрируется при доступной конфигурации;
нет подмены отсутствующих весов учебными находками.

Обязательная сдача: code + meaningful tests + точный input profile +
валидный пример + результат исходного открытого файла (ignored var) либо
точный gate, препятствующий реальному запуску. Изображения/числа из разных
корпусов не становятся одним пациентом. Модульный результат не равен
диагнозу; каждый clinical predicate имеет проверенную цитату и mapping.

Клинические правила текущего этапа остаются исследовательскими,
`requires_clinical_review`. Применение правил по фактам не делает код
зарегистрированным медицинским изделием.

## Итог реализации

Три агента последовательно выполнили пять независимых задач: ЭМГ/3D,
тепловизор/ноцицепция и УЗИ. Общий backend, анкету, применение правил,
отчёт и UI объединил интегратор. Дополнительная проверка УЗИ выполнялась
отдельным агентом в режиме чтения. Владение общими файлами не пересекалось.

- ЭМГ: полный реальный HipBridge CSV/gzip → 134 Welch MDF окна.
- Тепловизор: реальный FLIR JPEG → температурная матрица, автоматическая
  область, среднее/максимум, mask/heatmap.
- 3D: реальный DEPTH16 и original XML → автоматический объект, облако,
  внешние размеры с непроверенной геометрией камеры.
- УЗИ: реальный PNG → native UltraSAM3 text-prompt mask; пригодность
  анатомии/измерений отдельно проверяется QC, без ручного ROI.
- Ноцицепция: документированный четырёхколоночный экспорт → записи,
  события, ЧСС и QC; прогон явно синтетический, реальный export отсутствует.
- Анкета: исходные ответы → source-bound NICE referral rule с trace и цитатами.

Полный статус, входы, команды и границы диагностического применения —
[в инструкции общего запуска](run_raw_modules_ru.md).
