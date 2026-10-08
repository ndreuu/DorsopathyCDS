# T10 — Отдельный исследовательский дисковый детектор

Это параллельный трек после первого сквозного технического прогона.
Репозиторий `/Users/andrew/Projects/DorsopathyCDS`. Прочитай `AGENTS.md`,
общий brief, frozen ROI/evidence договорённости T00 и MRI-данные read-only.

## Владение

Новые `research/disc_bulging/` для тренировочного кода/протокола,
`src/dorsopathy_modules/mri_disc_features/` для лёгкого provider wrapper,
`src/dorsopathy_workers/disc_features/`, `tests/test_disc_features_provider.py`,
`docs/disc_bulging_experiment_ru.md`. Checkpoints, ROI и reports вне Git.
Не меняй текущий platform/MRI pipeline, source datasets или их split-файлы.

## Исходная точка

Parent `/Users/andrew/.codex/worktrees/7179/med diploma`:
`data/spider/disc-reference/manifest.json`, `artifacts/spider-disc-reference-audit.json`,
`scripts/prepare_disc_dataset.py`. На момент аудита: 64 пациента, 447 reference ROI,
216 bulging positive/231 negative; herniation positive всего 20.
Обученного per-disc детектора сейчас нет. Повторно проверь counts/splits/labels.

## Работа

1. Выбери один endpoint — binary disc bulging. Проверь происхождение меток,
   patient-level splits и утечки. Заранее зафиксируй test и тренировочный протокол.
   Не объявляй относительный ordinal диска подтверждённым L4-L5.
2. Реализуй воспроизводимое baseline training и meaningful сравнительный
   эксперимент. Тяжёлые зависимости в отдельном окружении. Если переиспользуется
   готовая модель, проверь доступ к весам/лицензии и сопоставимость endpoint.
3. Сохрани run config, seed, preprocessing, epoch selection, checkpoint hash,
   метрики на validation/test и ошибки/необработанные ROI. Не выбирай лучший
   вариант по test и не обещай заранее качество.
4. Provider выдаёт `spine.disc.bulging`, model_predicted, model identity/version/
   weights_sha256 и evidence на ROI/source frames. Unit для boolean = null.
   Threshold классификатора документируется; score не выдается за вероятность диагноза.
5. Отдельно оцени reference ROI и TSS-predicted ROI. Первый результат не является
   качеством всей MRI-системы; TSS использовал SPIDER, независимость end-to-end
   исследования требует отдельного учёта и не объявляется автоматически.
6. После приёмки зарегистрируй provider через T00 и подключи dependency в T05.

## Приёмка

- Повторяемое обучение/оценка и проверенные пациентские splits.
- Sensitivity/specificity, F1/confusion matrix и число случаев/ошибок;
  uncertainty оценок указана, severe/hernia accuracy из bulging не выводится.
- Настоящий checkpoint запускается в отдельном worker; synthetic не маскирует его.
- `needs_confirmation`, missing/unusable ROI и отказ модели сохраняются.
- Результат связан с диском/уровнем по реально доступной нумерации.
- ROI диска не называется контуром выпячивания, диаметр/объём грыжи
  из классификатора не придумываются. Такой сегментатор — отдельная задача.

Ресурсы/запуск обучения согласуй с интегратором, чтобы не занять MRI-slot.
Сдай training code, protocol, checkpoint location/hash, report и inference command.
