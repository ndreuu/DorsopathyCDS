# T08 — Интерфейс полного эпизода и просмотр evidence

Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief и frozen API/report/evidence fixtures T00.

## Владение

Новый `src/dorsopathy_viewer/` с bundled HTML/JS/CSS,
`tests/test_viewer_report.py`, `examples/viewer/`, `docs/episode_viewer_ru.md`.
Не меняй API, existing CDS-web, bootstrap/CLI, schemas или MRI-рендерер.
Packaging/bootstrap подключает T00 по предложенному entrypoint/assets API.

## Работа

1. Экран: создать эпизод, выбрать synthetic/replay/live, upload,
   анкета/карта боли, отдельные поля врачебных оценок, запуск и polling.
2. Результаты: жалобы, clinical assessment, MRI-наблюдения и задания.
   Покажи метод, origin, quality, единицы, уровень и состояние нумерации.
3. Для observation открой связанный PNG/срез, контур/ROI и измерение.
   Формат evidence согласован с T03; viewer не вычисляет свою площадь или диагноз.
4. Различай anatomical contour, disc ROI и pathology contour. Без детектора
   не называй маску диска грыжей. Площадь канала не означает размер выпячивания.
5. Clinical report показывает условия, прямые цитаты, lineage и unknown.
   Экспорт JSON/HTML использует серверный frozen report, а не заново собранный UI-state.
   После завершения MRI разреши врачебную оценку соответствия точного result ID;
   новый review создаёт отдельный report/snapshot и явно reuse MRI-анализ.
6. Обрабатывай queued/running/partial/failed, отсутствующие приборы и невозможность
   оценки. Безопасно показывай пользовательские строки, без innerHTML из uploads.

## Приёмка

- Сначала весь путь работает на согласованных fixtures, затем на T06 без
  смены полей/маршрутов. Mock mode явно отображается и не включается незаметно.
- Видны synthetic/replay/live, неподтверждённый уровень, непригодное измерение.
- Правильный кадр/контур/линия имеют ссылку на конкретное наблюдение.
- Нет утверждения «болезнь исключена» из not_met или «норма» из failed.
- Перезагрузка страницы открывает сохранённый workflow/snapshot, а не теряет отчёт.
- Проверены UI success/unknown/failure и экспорт; зафиксированы скриншоты
  только разрешённого демонстрационного примера в игнорируемом runtime-каталоге.

Сдай коммит, URL/инструкцию просмотра и результат взаимодействия в браузере.
