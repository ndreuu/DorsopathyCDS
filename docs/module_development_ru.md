# Как независимо разрабатывать модуль

## Что есть сейчас

Обычный registry содержит восемь провайдеров версии `0.1.0`: `complaints`,
`clinical.exam`, `mri.anatomy`, `lidar.posture`, `thermography`, `emg.surface`,
`ultrasound`, `nociception`. Полный учебный поток использует синтетические
исходные данные, а `mri.anatomy` также поддерживает нормализованный replay
и опциональный внешний live-worker. Отдельная команда `demo` запускает
только синтетические `demo.complaints` и `demo.mri_anatomy`. Исследовательский
`mri.pathology` регистрируется лишь при явной доверенной конфигурации worker;
`mri.disc_features` и `mri.muscles` в обычный registry не входят.
Общая версия wire-контракта — `1.0`; версия провайдера обновляется независимо.

Каждый модуль получает запрос, читает перечисленные артефакты, записывает
производные файлы и возвращает наблюдения. Обмен между модулями идёт через
платформу и артефакты; один провайдер не запускает другой.

## Каталог первого этапа

| module_id / пакет | Вход | Первый выход и единицы | Зависимость и граница |
|---|---|---|---|
| `complaints` / `complaints/` | Ответы анкеты, выбранные зоны на карте тела | `symptoms.pain_intensity`, `{score}`; карта боли отдельным артефактом | Не зависит от МРТ; происхождение `patient_report`; не классифицирует боль как психогенную |
| `clinical.exam` / `clinical_exam/` | Структурированный ввод врача | Подтверждённые элементы осмотра с `clinician_entered` | Отдельный контракт ввода; отсутствие осмотра не равно отрицательному тесту |
| `mri.anatomy` / `mri_anatomy/` | Сначала сохранённый экспорт TSS/SpinoSarc и соответствующие изображения | `spine.canal.cross_sectional_area`, `mm2`; маски и ROI как артефакты | Импорт уже полученного результата; сохраняет метод, геометрию, уровень и состояние нумерации |
| `mri.disc_features` / `mri_disc_features/` | ROI диска, указанный уровень, версия анатомического экспорта | `spine.disc.bulging`, boolean; evidence = ROI/исходные срезы | Можно обучать на готовых ROI параллельно анатомии; версия модели и SHA весов обязательны для `model_predicted` |
| `mri.muscles` / `mri_muscles/` | Экспорт MuscleMap/SpinoSarc с аксиальным изображением | `muscle.cross_sectional_area`, `mm2`, по мышце и стороне | Отдельный метод измерения; не устанавливает диагноз саркопении |

Стартовые concepts — соглашение разработчиков. Валидатор проверяет форму
кода и данные, но пока не содержит медицинского словаря. Новые concepts,
единицы и смысл полей согласовываются с владельцем платформы до интеграции.
Для нового понятия нужно описание значения, допустимых категорий и метода;
одинаковые числовые значения разных методов не считаются взаимозаменяемыми.

Направления `emg.surface`, `lidar.posture`, `thermography`, `ultrasound`
и `nociception` — [T11–T16](agent_tasks/README.md) — имеют независимые пакеты
в `src/dorsopathy_modules/`, объявленные входные профили и собственные фикстуры.
Для нового метода сначала согласуются declared input profile, данные и при
необходимости устройство, формат экспорта и воспроизводимый протокол.
Определять общий «датчик боли» без этих условий не следует.

`anatomy.region` использует `lower_back` для субъективной внешней зоны и
`lumbar_spine` для внутренней анатомии; эти зоны не являются точной регистрацией.
Уровни записываются единообразно: `L4-L5`, `L5-S1`. Неподтверждённая нумерация
обязательно остаётся `needs_confirmation`. Для мышц используется
`anatomy.region` с кодом конкретной мышцы, например `psoas`; клинический
словарь мышц согласуется при реализации адаптера.

## Общая граница в коде

Ориентир — `src/dorsopathy_modules/demo/complaints.py` и `demo/mri.py`.

```python
from dorsopathy_platform.contracts import validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope

class MyProvider:
    spec = ProviderSpec("mri.anatomy", "0.1.0", "Импорт MRI-анатомии")

    def analyze(self, request, context):
        source = request["input_artifacts"][0]
        with context.artifacts.open(source) as stream:
            # Прочитать нужный формат, проверить пригодность и выполнить задачу.
            pass
        # Заготовка: пока нет реализованного измерения, возвращаем partial.
        result = result_envelope(request, [], [], status="partial", warnings=[{
            "code": "adapter_not_implemented",
            "message": "No measurement was produced.",
        }])
        return validate_result(result, request)
```

Это заготовка интерфейса, а не реализация MRI-адаптера. В полном провайдере
`context.artifacts.add_file(path, media_type, role="derived")` импортирует
маски/изображения потоково и возвращает `ArtifactRef`. `add_bytes` и `read`
подходят для небольших JSON. `open` сначала проверяет хеш и размер, затем
возвращает файл с позицией 0; вызывающий закрывает его через `with`.

Каждое оценённое наблюдение содержит ненулевое значение и минимум одну
`evidence_refs`. Числа имеют единицы; boolean/категория — `unit: null`.
`not_assessed` и `not_applicable` имеют `value: null`. Отрицательный признак —
это `assessment_status: "assessed"`, `value: false`, а не пропущенное наблюдение.
Непригодное измерение сохраняет `quality: "unreliable"`; это не норма.

Для нескольких уровней/сторон одного concept используйте
`observation_id_for(job_id, concept, instance_key="L4-L5.left")`.
Модель указывается объектом `id/version/weights_sha256`, а не названием пакета.
В контракте 1.0 отдельного поля `model_score` нет. Добавлять его без изменения
контракта нельзя; числовая диагностическая вероятность не вычисляется.

`acquired_at` относится к получению исходных данных; неизвестное время — `null`.
`coordinate_frame` описывает известную систему координат, например
`DICOM-LPS-mm`; формат selector для среза/ROI документируется конкретным
модулем. `selector` — JSON, платформа не подтверждает, что указанная область
соответствует патологии. Для синтетических учебных данных origin — `synthetic_demo`;
реальное предсказание имеет `model_predicted`, измерение —
`automatically_measured`. Эти происхождения нельзя подменять друг другом.

## Проверка без запуска других модулей

Контрольная пара полных wire-документов:
`examples/platform/module_request.json` и `module_result.json`.
Она синтетическая и использует тот же контракт, что runner.

```sh
PYTHONPATH=src python3 -m dorsopathy_platform validate-result \
  --request examples/platform/module_request.json \
  --result examples/platform/module_result.json
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_platform*.py' -v
```

JSON Schema полезна внешним потребителям. Python-валидатор дополнительно
проверяет совпадение идентификаторов задания, уникальность наблюдений,
разрешение evidence и семантику статусов; одной проверки схемы недостаточно.

К сдаче модуль приносит собственные фикстуры, проверку реального преобразования
или измерения, пример частичного результата/отказа и инструкцию запуска.
По MRI отдельно проверяются правильность геометрии/уровней и качество признака.
Контур диска или ROI не называется контуром выпячивания без соответствующей модели.

## Параллельная работа в Git

Для каждого направления — отдельная ветка и worktree от общей интеграционной
ветки `main`. Рекомендуемые имена: `feature/complaints`,
`feature/mri-anatomy-adapter`, `feature/disc-bulging`, `feature/module-viewer`.
Эти ветки пока не созданы. Пример подготовки рабочего места:

```sh
git worktree add ../DorsopathyCDS-mri -b feature/mri-anatomy-adapter main
```

Владелец меняет свой provider-пакет, собственные тесты и инструкцию.
Схемы, runner, storage и общий каталог меняет интеграционный владелец.
Сначала согласуйте фикстуру результата; после этого UI и потребители могут
разрабатываться на ней, пока модель ещё обучается. Интеграция заменяет fixture
реальным результатом и повторяет тот же контрактный проход.

Общая платформа реализует HTTP для заданий и артефактов, `ProcessProvider`
для отдельного MRI-окружения, лимиты ресурсов и восстановление заданий после
сбоя. Эти механизмы принадлежат платформе; каждый аналитический провайдер
остаётся владельцем собственного входного профиля и преобразования данных.

См. [архитектуру](architecture_ru.md) и
[границы владения и порядок интеграции](parallel_development_ru.md).

## Опциональный исследовательский модуль MRI pathology

Согласованный независимый модуль — `mri.pathology`, версия `0.1.0`,
отображаемое имя `MRI pathology (SpineNet research)`. Владение реализацией:
`src/dorsopathy_modules/mri_pathology/` и отдельный внешний worker с тяжёлыми
зависимостями. Платформа регистрирует только `ProcessProvider`; она не
импортирует SpineNet, PyTorch или MRI-окружение. Модуль получает собственные
исходные MRI-артефакты и не требует результата `mri.anatomy`. Первый входной
профиль — один 3D scalar volume в embedded MHA или NIfTI, пригодный для
сагиттальной T2-обработки, с изотропным in-plane spacing. Worker приводит
ориентацию к PIR без интерполяции; DICOM ZIP в этот профиль не входит.

Первый поддержанный набор concepts:

| concept | Значение и единицы | Граница интерпретации |
|---|---|---|
| `spine.central_stenosis.grade` | Категория `none`, `mild`, `moderate`, `severe`; `unit: null` | Четыре класса собственной шкалы компрессии SpineNet; не шкала Schizas/RSNA и не измерение площади канала |
| `spine.disc.herniation.present` | Boolean; `unit: null` | Бинарный выход модели о наличии грыжи; без стороны, морфологии, размера в мм или контура патологии |

Наблюдения относятся к уровням `L1-L2`, `L2-L3`, `L3-L4`, `L4-L5`, `L5-S1`.
Отсутствующий или непригодный для модели уровень остаётся `not_assessed` с
`value: null`; отсутствие выхода не превращается в `none` или `false`.
Нумерация модели остаётся `needs_confirmation`. Оценённые выходы имеют
`origin: model_predicted`, описание метода, версию модели и SHA256 весов,
а также evidence на исходные изображения и точный модельный crop/ROI.
Crop/ROI показывает контекст, использованный моделью; это не контур стеноза
или грыжи. Другие выходы, включая Pfirrmann/дегенерацию и фораминальный стеноз,
в этот первый контракт не включены.

Без доверенного файла конфигурации модуль отсутствует в registry. Нет
синтетического fallback и статуса готовности без реально выполненного worker.
Конфигурацию выбирает локальный оператор; загруженные пациентские артефакты
и параметры задания не могут задавать команду процесса. Файл ограничен
65536 байтами и содержит только `command` (непустой массив argv без shell)
и `timeout_seconds` (конечное число от 1 до 3600). Например:

```json
{
  "command": [
    "/absolute/path/to/python", "-m", "dorsopathy_workers.mri_pathology",
    "--upstream-root", "/absolute/path/to/SpineNet",
    "--contrast", "T2", "--device", "cpu"
  ],
  "timeout_seconds": 600
}
```

Пути в примере нужно заменить на отдельное MRI-окружение и проверенный
upstream SpineNet. Модуль `dorsopathy_workers.mri_pathology` должен быть
доступен этому Python; при использовании `PYTHONPATH` передайте абсолютные
пути к `src` и установленным зависимостям, поскольку worker запускается в
собственном exchange-каталоге. Запуск через CLI:

```sh
PYTHONPATH=/absolute/path/to/DorsopathyCDS/src:/absolute/path/to/DorsopathyCDS/var/spinenet-deps python3 -m dorsopathy_platform serve \
  --store var/multimodal --mri-pathology-config /absolute/path/to/pathology-config.json
PYTHONPATH=/absolute/path/to/DorsopathyCDS/src:/absolute/path/to/DorsopathyCDS/var/spinenet-deps python3 -m dorsopathy_platform run-flow \
  --store var/multimodal --manifest /absolute/path/to/manifest.json \
  --mri-pathology-config /absolute/path/to/pathology-config.json
```

Рабочие файлы процесса находятся в `<store>/mri-pathology-workers`, отдельно
от `<store>/mri-workers`. Оба MRI-модуля используют последовательную MRI-очередь
платформы; эта граница ограничивает ресурсы и не создаёт зависимости между
их результатами. Clinical allowlist и клинические правила не меняются:
предсказания этого исследовательского модуля не становятся автоматически
подтверждёнными клиническими фактами.
