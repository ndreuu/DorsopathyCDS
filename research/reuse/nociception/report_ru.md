# T15: переиспользование ANI / PMD, исследование без реализации

Проверено 09.10.2026 (Europe/Istanbul). **Рекомендация: импортировать сохранённый индекс конкретного прибора; начать с pandas + Plotly. Проверенного открытого исходного ANI/NOL-export с timestamp/quality/events не найдено.** Есть открытые производные NOL-данные PMD-100, пригодные для отдельного исследовательского просмотра. Ни импорт, ни аппаратный захват здесь не реализованы.

## Что отдаёт прибор

**MDoloris ANI Monitor V2.** Производитель документирует USB-export текстовых файлов, имя по дате/времени начала. Поля: Date `DD/MM/YYYY`, Time `HH:MM:SS`, Time(s), Energy, ANI (Expert), ANImean, ANIc (Classic), Quality, Events. Quality имеет коды 0–3: 0 плохое, 1/2 среднее, 3 хорошее. Индекс 0–100 безразмерный; ANIi усредняется за 120 s, ANIm за 240 s; базовое окно 64 s обновляется каждую секунду. Нельзя автоматически отождествлять все экспортные имена с ANIi/ANIm без конкретного файла. Energy оставлять с неизвестной единицей. Разделитель, локаль чисел, timezone, пропуски и связь колонок с режимом требуют эталона. Источник: [manual v.10, 19.09.2022, pp.11,29–32](https://www.mdoloris.com/wp-content/uploads/2024/06/ANI-MONITOR-V2-User-Manual-V10-EN.pdf). PDF напрямую отвечал 403/521; эти разделы проверены через индексированный текст производителя. Полноценный файл экспорта не получен.

Real-time RS-232/DE-9 и USB подтверждены [product sheet v.05](https://academic.mdoloris.com/wp-content/uploads/2024/03/MDQUAEN51.ANIV2-v.05-Product-sheet.pdf). Serial protocol выдаётся через представителя MDoloris (manual p.32); открытого SDK/parser с проверенным эталоном не найдено. ANI-MR — отдельная модель, её инструкцию не подменяем инструкцией V2.

**Medasense PMD-100.** Это подтверждённая исследовательская модель NOL, а не опечатка PMD-200. В [оригинальной работе Edry 2016, p.194](https://medasense.com/wp-content/uploads/2019/12/Preliminary-Intraoperative-Validation-of-the-Nociception-Level-Index-A-Noninvasive-Nociception-Monitor.pdf) PMD-100 записывали через GE S/5 Collect каждые 5 s. [Bollag 2018](https://f1000research.com/articles/7-875) использовал Medasense software на ноутбуке и согласовал часы с AIMS. Это сведения о двух исследовательских цепочках, а не публичная спецификация PMD-100. Его manual, SDK и byte-level export не подтверждены; нужен точный прибор/software и разрешённый файл.

**PMD-200, system 2.2.1.** [EU manual MK2U-02-378 Rev.4, pp.14–15,47–48](https://medasense.com/wp-content/uploads/EU-User-Manual-2.2.1-MK2U-02-378-Medasense-PMD-200.pdf) описывает proprietary algorithms, USB CSV `Time / NOL index / Heart rate / Annotations`, полный raw-folder и Review Session PDF. RS-232 допускает Medasense-approved systems; публичного протокола/SDK в проверенных источниках нет. CSV-список не обещает отдельный quality-column: отсутствие индекса нельзя заменять нулём. NOL 0–100 безразмерный; окно алгоритма, cadence, encoding/timezone и raw-folder schema пока неизвестны. [US manual MK2U-02-708 Rev.2, pp.36,39–42,55](https://medasense.com/wp-content/uploads/2025/11/Medasense-PMD-200-User-manual-2.2.1-MK2U-02-708-English-US.pdf) подтверждает подавление NOL при непригодном сигнале и влияние бодрствования/пробуждения. Эти документы не подтверждают формат PMD-100.

## Открытые данные: что реально проверено

1. **Bollag NOL dataset**, [DOI](https://doi.org/10.5256/f1000research.15279.d207164), CC0. Проверен [реальный TGZ 18 792 B](https://www.ebi.ac.uk/biostudies/files/S-EPMC6039929/f1000research-7-16645-s0000.tgz): XLSX 21 297 B, листы `DEMOGRAPHICS`, `NO EPIDURAL`, `EPIDURAL`. Колонки NOL/HR до события −60…−10 s, после +10…+180 s, delta; события Intubation/Incision. **Это агрегаты по окнам, не timestamped vendor-export**, нет ряда Quality/абсолютных часов. Контекст VATS под общей анестезией; не корпус дорсопатий. Данные просмотрены в памяти; в Git не добавлены.
2. **[OpenNeuro ds008115 v1.0.0](https://github.com/OpenNeuroDatasets/ds008115)**, CC0, первая версия 07.07.2026. Проверены README/CHANGES/metadata и `sub-006`: четыре EEG-канала Masimo SedLine, событие Positioning onset 240 s. Имена 1089 файлов не выявили NOL/PMD/physio-export. Название ValidPain2 и упоминание NOL в статье не делают этот опубликованный EEG файл экспортом NOL. Статус: не подходит для vendor-import.
3. Отдельный generic replay: **[PhysioNet Autonomic Aging 1.0.0](https://www.physionet.org/content/autonomic-aging-cardiovascular/1.0.0/)**, WFDB `.hea/.dat`, ODbL-1.0; проверен каталог файлов, большие записи не скачивались. Это ECG/автономные сигналы, не ANI/NOL.
4. **[BioVid](https://www.nit.ovgu.de/BioVid.html)** — экспериментальная тепловая боль, ECG/GSR/EMG. Только non-commercial research, подписанное соглашение и одобрение доступа; файлы/их encoding не получены. Не свободный vendor-index корпус и не диагностический корпус позвоночника.

## Готовые инструменты

| Кандидат | Кодовая лицензия | Роль и ограничение |
|---|---|---|
| [pandas](https://github.com/pandas-dev/pandas), [read_csv](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html) | BSD-3-Clause | CSV/TXT, явные dtype/NA/locale; не vendor-driver. XLSX требует отдельного engine. |
| [Plotly.py](https://github.com/plotly/plotly.py) | MIT | Локальный HTML-график индекса, событий, gaps; не клиническая интерпретация. |
| [pySerial](https://github.com/pyserial/pyserial), [API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html) | BSD-3-Clause | Только транспорт RS-232 после получения протокола/разрешённой конфигурации и hardware-проверки. |
| [WFDB-Python](https://github.com/MIT-LCP/wfdb-python) | MIT | Generic signal/annotation replay; не читать им proprietary-export и не вычислять ANI/NOL. |

Открытые веса/разрешение воспроизводить фирменный алгоритм не найдены. Лицензии библиотек не предоставляют прав на приборный SDK или чужие данные.

## Минимальный профиль и gates

Предлагаемый manifest: manufacturer/model/serial без идентификаторов пациента, software/manual version, session/subject linkage, protocol/state (awake/sedated/general-anesthesia), acquisition interval, raw field names, index kind/scale/unit=`1`, sample period, averaging window, local timestamp/timezone, clock domain/offset/uncertainty, quality raw + mapping, event label/onset/duration, missing-value encoding. Неизвестное хранить `null`; timezone компьютера не присваивать прибору. Без clock/quality/window нельзя заявлять временное сопоставление.

Для ModuleResult 1.0: исходный файл и каноническая series — artifacts с SHA256/role; scalar observation только для согласованной точки/окна, `origin=automatically_measured`, unit `1`, method с adapter/version, `acquired_at=null` при неизвестной timezone, quality unknown/unreliable по доказательствам. `evidence_refs` указывает artifact и row/channel/window/event selector; график не заменяет источник. Series/context живут в artifact, не в новых полях Observation. Проверка основана на `src/dorsopathy_platform/contracts.py`; concepts/selector согласовать с T00 до реализации.

**Synthetic gate:** parser/NA/decimal precision, rollover, duplicates/gaps, несинхронные clocks, плохое качество, unknown window, event selection и evidence; origin только `synthetic_demo`. **Real-import gate:** конкретный неизменённый разрешённый export + сверка с прибором. **Capture gate:** hardware, cables, vendor protocol/SDK terms, проверка timestamps/quality. **Clinical gate:** отдельная применимость к бодрствующему пациенту с дорсопатией. Индекс не определяет отсутствие, причину боли или правдивость жалоб. Все gates пока не выполнены: результат этой работы — подбор зависимостей.
