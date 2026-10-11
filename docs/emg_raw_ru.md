# Поверхностная ЭМГ: исходный экспорт и изолированный signal worker

Новый независимый `RawEMGProvider`: `emg.surface/0.2.0`, профиль
`emg-raw-csv.v2`, результат `ModuleResult 1.0`. Исходный
`EMGSurfaceProvider/0.1.0` и `emg-csv.v1` сохраняют прежний метод.
Новый профиль не принимает готовые RMS, контуры, выбранные вручную окна или
заключение. На входе исходный CSV/gzip и сведения получения записи.

## Вход и доверенная конфигурация

[Синтетический acquisition manifest](../examples/emg_raw/synthetic-acquisition.json)
и [HipBridge acquisition manifest](../examples/emg_raw/hipbridge-acquisition.json)
задают полную форму. `csv_artifact_id` заменяется после загрузки исходного файла.
Request содержит ровно два source artifacts, пустые parameters и совпадающий
protocol_id. Допустимые export.format: `csv`, `csv_gzip`,
`hipbridgeemg.v0.1`. Последний проверяет опубликованные колонки и 1000 Hz.
Time/channel/ignored columns объявлены явно: названия каналов не угадываются
по амплитуде. Игнорирование прочих каналов не изменяет выбранные отсчёты.

Каждый канал отдельно сохраняет сторону, название мышцы, placement,
calibration и ADC rails. `uV_per_unit` применяется только при
`status=verified`, указанном source и author_id; неизвестный gain не выводится
из марки прибора или названия корпуса. Device serial, upstream filtering,
absolute time, clock domain, точный уровень и scale допускают null. Общая
calibration описывает получение всей записи; амплитудный gain принадлежит
конкретному каналу. Software не поверяет прибор.

Events — только записанные acquisition annotations с start_s/stop_s/source.
Без events анализируются окна всей записи: фазы, количество сокращений и
сторона задания неизвестны. Общая инструкция «три движения каждой ногой»
не задаёт время реальных движений. MVC, нормализация и сравнение сторон
сейчас остаются null: одинаковое имя мышцы не доказывает сопоставимость
gain, положения электродов, усилия и протокола.

Оператор задаёт путь к Python с NumPy/SciPy; ModuleRequest не может задавать
команду, Python или host paths:

```python
from dorsopathy_modules.emg_surface import RawEMGProvider
provider = RawEMGProvider(
    python_executable="/absolute/path/to/emg-environment/bin/python",
    workspace_root="/absolute/path/to/ignored/emg-workers",
    timeout_seconds=120,
)
result = provider.analyze(request, context)
provider.close()
```

Worker также совместим с платформенным ProcessProvider:
`python -m dorsopathy_workers.emg --exchange-dir <private-exchange>`.
Нужен абсолютный PYTHONPATH к src или установленный пакет. Импорт provider
не импортирует NumPy/SciPy. Проверенная optional среда использует Python 3.12.
requirements-emg.txt фиксирует среду проверенного
метода; версии реально использованных библиотек сохраняются в method artifact.

## Обработка, QC и смысл показателей

Настройки обработки обязательны. Нет неявного notch, resampling,
заполнения пропусков, MVC или диагностического порога.

1. Проверяются уникальные headers, размер, монотонность времени, конечность
   и регулярность сетки, missing/NaN/Inf, известные ADC rails.
2. Для каждого канала выделяются непрерывные пригодные участки. Пропуск,
   clipping или временной разрыв разрывает участок; фильтр не проходит через него.
3. Объявленный constant detrend вычитает среднее участка; optional Butterworth
   bandpass и optional IIR notch применяются последовательно в SOS форме,
   forward/backward `sosfiltfilt`, odd padding, с явным padlen.
   Полоса и notch строго ниже известной Nyquist. Filter edge guard обязателен
   и не меньше padding; это техническое исключение края, не доказанная оценка
   всех переходных эффектов фильтра.
4. Envelope — центрированное скользящее среднее абсолютного обработанного
   сигнала, нечётное число отсчётов, без padding. Общий guard равен максимуму
   filter guard и половины envelope. Fixed windows, касающиеся guard/пропуска/
   clipping или неполные, остаются not_assessed.
5. `emg.rms`, uV = sqrt(mean(filtered_x²)).
   `emg.envelope`, uV = среднее envelope в данном окне; сам временной envelope
   сохранён в processed.csv. Оба физические показателя требуют gain канала.
6. `emg.median_frequency`, Hz = первый bin, в котором накопленная Welch PSD
   достигает половины суммы равных по ширине bins. Periodic Hann, явные
   nperseg/noverlap/nfft, detrend=False, density, mean, one-sided.
   PSD сохраняется как uV²/Hz либо source_units_squared/Hz, не как uV при
   неизвестном gain. MDF — discrete-bin величина без интерполяции;
   resolution=fs/nperseg. Нулевая мощность даёт MDF null/unreliable.

[Описание SciPy SOS filtering](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html)
и [Welch PSD](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)
определяют программные методы, не медицинские нормы. Missing rails дают
clipping_unverifiable/unknown; отсутствие Fs блокирует частотные фильтры и MDF.
Invalid calibration блокирует признаки, unknown calibration блокирует uV.
Unknown/unreliable не означают нормальную функцию или отсутствие заболевания.

Результат содержит raw+manifest, processed CSV, PNG/SVG, method JSON,
measurement/QC JSON. Evidence каждого observation включает исходный канал,
0-based sample_start/stop_exclusive, acquisition metadata/event, processed
интервал и method artifact. Массивы PSD и сигналов остаются artifacts.
Observation.acquired_at — время начала окна, только если известно время
начала записи. Clock uncertainty/синхронизация не возникают из ISO timestamp.

Пределы: raw/expanded CSV до 64 MiB, до 1 000 000 строк и 8 каналов,
до 8192 channel-windows и 8 Mi отсчётов в их суммарном расширении.
Welch nperseg 16…4096, окно 16…16384, bandpass order 1…8.
Этот профиль не распознаёт motion/crosstalk/плохой контакт по одному признаку;
отсутствие обнаруженного clipping не удостоверяет качество электродов.

## Реальный исходный файл

[HipBridgeEMG v0.1, DOI 10.5281/zenodo.17465228](https://zenodo.org/records/17465228),
CC BY 4.0: Scheurer Sebastian (data curator), Tedesco Salvatore (data collector),
2025. Из опубликованного raw `fd0f5636-phe-1.csv.gz` импортируется вся запись
34963 строк, два bilateral erector spinae longissimus channels; 1000 Hz,
FREEEMG/SENIAM по описанию автора. Исходный SHA256:
`e8095124581037c7e46f9ea097ad5fda57c62771747706d9bf73da9aeba5c4b6`.
Оригинал уже сохранён вне Git в
`var/real-inputs/emg/hipbridge-phe-v0.1/original/`.

```sh
python3 scripts/run_emg_raw.py \
  --csv var/real-inputs/emg/hipbridge-phe-v0.1/original/fd0f5636-phe-1.csv.gz \
  --manifest examples/emg_raw/hipbridge-acquisition.json \
  --python-executable /absolute/path/to/emg-environment/bin/python \
  --output var/emg-raw-hipbridge-v2
```

В этой записи единицы амплитуды/gain/ADC rails/MVC, точное положение по уровню,
absolute time и upstream filtering не подтверждены. Поэтому RMS/envelope
в uV остаются null; MDF и filtered/envelope arrays в source units считаются
при пригодной сетке, с quality unknown. Разметка фаз не опубликована и не
выдумывается. Этот здоровый exercise corpus не имеет диагностической разметки
и не объединяется с MRI/другими участниками в одного пациента.

Synthetic тесты проверяют известные RMS/частоты/полосу фильтра, gap/Inf/clipping,
персональный gain, provenance, реальные events и subprocess wire. Это проверка
программного метода, не клиническая валидация:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_emg_raw.py' -v
PYTHONPATH=src /absolute/path/to/emg-environment/bin/python -m unittest discover \
  -s tests -p 'test_emg_raw.py' -v
```

Поверхностный RMS/envelope/MDF не устанавливают радикулопатию, усталость,
причину боли, норму, психогенную боль или достоверность жалоб; не заменяют
игольчатое EDX. Клинические правила остаются отдельным проверяемым mapping.

Проверенный запуск 10.10.2026: \`var/emg-raw-hipbridge-v2/\` содержит
414 observations: 134 оценённых MDF, остальные physical amplitudes/краевые
окна не оценены. \`checks.json\` проверяет все 134 MDF независимым stdlib direct
DFT периодического Hann (без SciPy/NumPy FFT), исходные 34963 time/Frame rows
и envelope. Максимальная absolute PSD difference = 7.915e-18 source_units²/Hz,
envelope difference = 8.327e-17 source units. Это проверка вычисления, не
доказательство аппаратного качества, насыщения без rails или клинической роли.
