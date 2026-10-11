# Архив одного синтетического пациента

Архив: `var/bundles/synthetic-patient-001.zip`, 260 403 bytes (около 254 КБ).
SHA256: `a4296098f9c72eeab16dbc5e2fa56721ce0acf5f8c41ad0968d8044adcd9c391`.
Внутри 19 файлов: `case.json`, описание, ожидаемые учебные значения и входы
восьми модулей. Все данные сгенерированы с нуля; реальных записей человека нет.

Одна идентичность: `SYNTHETIC-PATIENT-001`, session `SYNTHETIC-SESSION-001`,
protocol `synthetic-patient-v1`, время `2026-01-01T12:00:00+00:00`.
Это единый программный fixture, без утверждения, что физический пациент может
одновременно находиться в разных положениях или что координаты зарегистрированы.

## Состав и проверенный результат

| Модуль | Что лежит в архиве | Что делает рабочий провайдер |
|---|---|---|
| Жалобы `0.2.0` | Выдуманные явные ответы: сильная боль, распространение в ногу, новое онемение промежности | Извлекает 8 фактов, source-bound NICE rule → `met`, срочная оценка |
| Осмотр `0.1.0` | Выдуманная врачебная форма: настораживающие признаки не исключены, тесты положительные, соответствие МРТ неизвестно | Триада КР РФ → `not_met`; неизвестное соответствие сохраняется |
| МРТ `0.1.0` | Source-grid фантом 96×96 с известной анатомической маской | Считает 176,64 мм² и создаёт mask/overlay; **нейросеть поиска патологии не запускается** |
| ЭМГ `0.2.0` | CSV, 8192 отсчёта двух синусов, события, nominal uV scale | Считает 10 пригодных окон, MDF 125/187,5 Гц, RMS и огибающую |
| 3D `0.2.0` | DEPTH16 PNG с учебной плоскостью и camera JSON | 1104 точки, extents 230×450×0 мм, PCA; это геометрический фантом, не позвоночник |
| Тепловизор `0.2.0` | Новый FLIR radiometric JPEG с математическим полем и коэффициентами | Штатный ExifTool → counts → Cel → автоматическая область: mean 32,939°C, max 34,503°C |
| УЗИ `0.2.0` | Procedural speckle PNG 512×384 без готовой маски/ROI | Настоящая UltraSAM3 → 3 кандидата, top score 0,659, маска 7192 px; размеры неизвестны из-за касания границы |
| Ноцицепция `0.2.0` | Выдуманный PMD-200 CSV из 9 строк и события | Mean index 32,556; ЧСС 74,333 /мин; качество и event delta неизвестны |

Результат текущего полного прогона: восемь результатов, **0 failed**,
все origins — `synthetic_demo`. Общий статус `partial`: часть клинических
сведений и измерений явно неизвестна. Никакой диагноз не подтверждён или исключён.
Score УЗИ — некалиброванный выход модели, не вероятность диагноза.

## Запуск готового ZIP

Из `/Users/andrew/Projects/DorsopathyCDS`:

```sh
PYTHONPATH=src python3 scripts/run_synthetic_patient.py \
  --archive var/bundles/synthetic-patient-001.zip \
  --raw-workers-config var/raw-workers.json \
  --store var/synthetic-patient-demo --output var/synthetic-patient-run
```

`var/raw-workers.json` — отдельная доверенная конфигурация уже настроенных
окружений/ExifTool/настоящей модели UltraSAM3; **команд и весов в ZIP нет**.
Готовый ZIP не требует Pillow для импорта. Runner проверяет inventory, SHA,
размеры, поля привязки и synthetic identity до создания episode; архив читается
потоково, без распаковки. Источники получают реальные загруженные artifact IDs.

Файлы результата: `var/synthetic-patient-run/{run,bundle,snapshot,report,summary}.json`
и `report.html`. Сам ZIP тоже сохранён как исходный артефакт episode.

Когда CLI завершился, открыть то же хранилище в браузере:

```sh
PYTHONPATH=src python3 -m dorsopathy_platform serve \
  --store var/synthetic-patient-demo --port 8769 \
  --raw-workers-config var/raw-workers.json
```

URL готового случая находится в `summary.json`. Запущенный пример:
<http://127.0.0.1:8769/#run=run-0e2cf31bacf641bab388a3708cb307d2>.
Нельзя запускать CLI над этим store одновременно с сервером: используйте другой
`--store` или остановите владельца текущего store.

## Пересборка

Нужен Python с Pillow только для JPEG encoding. Текущий imaging environment:

```sh
PYTHONPATH=src "/Users/andrew/.codex/worktrees/7179/med diploma/.venv-spinosarc/bin/python" \
  scripts/build_synthetic_patient.py --output var/bundles/synthetic-patient-001.zip
```

[Сборщик](../scripts/build_synthetic_patient.py),
[генераторы](../scripts/synthetic_patient/) и
[запуск](../scripts/run_synthetic_patient.py) находятся в репозитории.
Две независимые сборки в текущем окружении дали одинаковые bytes и SHA256.
В другом JPEG encoder/Pillow возможен другой ZIP hash при том же учебном поле.

В Git включаются генераторы и инструкции; ZIP, результаты и веса остаются в
ignored `var/`. Клинические правила/цитаты и границы модулей не менялись:
[общая инструкция](run_raw_modules_ru.md).
