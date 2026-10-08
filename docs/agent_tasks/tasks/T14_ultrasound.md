# T14 — УЗИ: кадр, масштаб и ручное измерение структуры

Работай в `/Users/andrew/Projects/DorsopathyCDS`, в своей ветке/worktree.
Прочитай AGENTS.md, общий brief и frozen контракты T00.

## Владение

`src/dorsopathy_modules/ultrasound/`, `src/dorsopathy_workers/ultrasound/`,
`tests/test_ultrasound.py`, `tests/fixtures/ultrasound/`,
`docs/ultrasound_module_ru.md`. Общие schemas/UI/clinical rules не менять.

## Работа

1. Определи первый endpoint: толщина выбранной мышцы по заданным точкам
   либо площадь по заданному контуру, при документированном масштабе кадра.
2. Импортируй кадр/DICOM-supported export, структуру/сторону, pose/protocol,
   acquisition metadata и проверенную калибровку в области измерения.
   Для PNG масштаб вводится отдельно с указанием источника/автора.
3. Не предполагай один mm/pixel для любого US/DICOM: учитывай ограничения
   physical calibration/region выбранного формата. Неподдерживаемая геометрия
   остаётся неоценённой. Точки/контур — annotation artifact с авторством.
4. Вычисли расстояние/площадь в mm/mm2, сохрани method, кадр и overlay/evidence.
   Автосегментация/воспаление не входят в этот первый endpoint.

## Приёмка

- Эталонный calibrated кадр проверяет расстояние/площадь и координаты overlay.
- Без масштаба возможны просмотр/разметка, а метрическое измерение not_assessed.
- Неверные точки, выход за calibration region и неоднозначные units отклоняются.
- Human annotation видна в методе; измерение не выдаётся за автоматическое
  обнаружение патологического очага. Эхогенность не называется жировой фракцией.
- Видны source frame, структура, сторона, pose и статус качества.

Сдай коммит, пример, real-export data gate и команду проверки. Открытый корпус
другой области тела годится для importer, но не доказывает поясничный endpoint.
