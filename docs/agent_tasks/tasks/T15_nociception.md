# T15 — ANI / PMD: импорт конкретного приборного индекса

Работай в `/Users/andrew/Projects/DorsopathyCDS`, в своей ветке/worktree.
Прочитай AGENTS.md, общий brief и frozen контракты T00.

## Владение

`src/dorsopathy_modules/nociception/`, `src/dorsopathy_workers/nociception/`,
`tests/test_nociception.py`, `tests/fixtures/nociception/`,
`docs/nociception_module_ru.md`. Клинический движок/common schema не менять.

## Работа

1. Уточни фактический прибор: производитель, модель/версия, индекс и доступный
   export protocol. Сначала primary manual/документация и разрешённый пример;
   обозначение PMD-100 из идеи заказчика не заменяй другой моделью молча.
2. Реализуй канонический импорт timestamped index/quality/events с исходной
   шкалой, units, averaging window, clock domain и context/protocol.
3. Vendor-adapter добавляй только для подтверждённого формата; universal CSV
   fixture остаётся каноническим примером, не «проверенным ANI-драйвером».
4. Artifact содержит series; observations — выбранные значения/окна по frozen
   definitions. График и evidence указывают время/окно/событие.
5. Не вычисляй фирменный ANI/NOL из произвольного HRV и не называй чужой сигнал
   экспортом прибора. Неизвестные units/quality/clock сохраняй явно.

## Приёмка

- Эталонный export fixture проверяет время, units, окна и отсутствие округления
  или ресэмплинга без метода. Real vendor export имеет отдельный пройденный тест.
- Gaps, плохое качество и неизвестное время исключают заявленное сопоставление.
- Нет интерпретации «боли нет», её причины или правдивости жалоб по индексу.
- Capture/SDK и проверка прибора — отдельный профиль; без устройства не считаются
  пройденными. Применимость протокола к проекту — отдельный clinical gate.

Сдай коммит importer, specs/source references, команду и статусы gates.
Недоступный открытый export — явная зависимость; fabricated vendor данные
не принимаются как завершённая интеграция прибора.
