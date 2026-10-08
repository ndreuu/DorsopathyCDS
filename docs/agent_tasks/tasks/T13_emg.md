# T13 — Поверхностная ЭМГ: сигнал и воспроизводимые признаки

Работай в `/Users/andrew/Projects/DorsopathyCDS`, в своей ветке/worktree.
Прочитай AGENTS.md, общий brief и frozen контракты T00.

## Владение

`src/dorsopathy_modules/emg_surface/`, `src/dorsopathy_workers/emg/`,
`tests/test_emg_surface.py`, `tests/fixtures/emg_surface/`, `docs/emg_module_ru.md`.
Общий dispatcher/API/клинический движок не менять.

## Работа

1. Выбери доступный signal export/открытый корпус и зафиксируй источник,
   лицензию, мышцу/каналы, физические единицы, sample rate и task/protocol.
   Статический скриншот сигнала не является исходной ЭМГ для вычисления признаков.
2. Контроль качества: missing samples, clipping, ограничения частоты и
   доступные признаки помех. Не выдумывай electrode placement/MVC calibration.
3. По известной частоте выполни документированную обработку с сохранением
   фильтров; RMS/envelope по окнам и частотный показатель при пригодном сигнале.
   Полоса фильтра должна соответствовать Nyquist, без silent defaults.
4. Raw/processed signals и графики — artifacts; evidence с channel/sample interval,
   acquisition time/clock domain и window. Крупные массивы не помещай в value.
5. Сравнение сторон/нормализация допустимы при совместимом протоколе и наличии
   требуемого эталона; иначе показатель не оценивается.

## Приёмка

- Эталонные сигналы проверяют RMS/частоту, единицы и оконную обработку.
- Пропуски/насыщение/неизвестная частота дают QC/partial, не «норму».
- Пользователь видит signal segment, обработку и measurement evidence.
- Поверхностные признаки не устанавливают радикулопатию и не заменяют
  игольчатую ЭМГ/ЭНМГ; никакой оценки правдивости жалоб.
- Реальный signal import отделён от synthetic и от аппаратного захвата.

Сдай коммит, source manifest, запуск и expected outputs. Непоясничный corpus
проверяет импорт/обработку, но не подтверждает исследование мышц поясницы.
