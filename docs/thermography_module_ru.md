# Термография: числовая матрица и выбранные ROI

`ThermographyProvider`, module `thermography`, version `0.1.0`.
Поддержанный профиль `thermography-matrix.v1` — **уже декодированный экспорт
температур Cel в JSON**, не универсальный importer FLIR/JPEG/SEQ/DJI.

## Вход и расчёт

[source.json](../tests/fixtures/thermography/source.json) — единственный source
ArtifactRef, media `application/json`, parameters пусты. Матрица `[row_y,column_x]`,
0-based integer ROI pixels `[x,y]`, оси x-right/y-down. Anatomical side задаётся
автором экспорта, не выводится из положения ROI на картинке.
Максимальные dimensions 1024×1024, source JSON 8 MiB. Ragged/non-finite matrix,
повторяющиеся ROI pixels и неоднозначные units отклоняются. Null pixels допустимы
как missing: measurement ROI с хотя бы одним missing/outside pixel не оценивается;
mean не вычисляется по неполному ROI без явной политики покрытия.

Calibration `verified` требует source и author_id; это declaration экспорта,
не доказательство поверки камеры. Unknown/invalid calibration даёт null metric
values с `not_assessed`. Emissivity, ambient_Cel и distance_m явно сохраняются;
при unknown conditions ROI numeric mean сохраняется quality=unknown, а pair delta
не оценивается. Температуры уже переведены в Cel до этого importer; коэффициенты
Planck и camera defaults этот provider не выбирает.

- `thermal.roi_mean_temperature`, Cel: sum всех ROI pixels / их count.
- min/max той же ROI сохраняются в measurement JSON.
- `thermal.left_right_delta`, K: **mean(left) − mean(right)**, только для
  same-matrix явно compatible pair с left/right labels и известными conditions.

Fixture `[[32,34],[32,34]]` даёт 32/34 Cel и −2 K. Изменение одного левого
pixel на 36 меняет левое среднее на 34 и delta на 0: никаких canned values.
Unknown/incompatible pair не превращается в равенство температур.

## Artifacts и проверка

Исходная matrix/ROI/calibration JSON, цветной preview SVG со шкалой Cel,
PNG и measurement JSON остаются доступными evidence. SVG использует source pixel
grid; PNG dimensions совпадают с матрицей, browser может увеличить его для просмотра.
Black pixels означают missing preview data, не 0 Cel. В sidecar сохраняются
source time/frame/pose/protocol, calibration и comparison_metadata. Ссылки
на metadata/calibration указывают настоящий источник; sensor-to-body transforms
и linkage не выдумываются. Synthetic и recorded origins различаются.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_thermography.py' -v
```

## Vendor/data gates

System Python не содержит ExifTool, Rscript/Thermimage, NumPy/Pillow; dependencies
не устанавливались. Device capture и vendor parser в provider **не реализованы**. Отдельный
локальный converter проверен на настоящем radiometric FLIR кадре спины,
с file-specific коэффициентами и spot check; decoded matrix прошла provider.
Права на пример unknown, clinical ground truth отсутствует. [Демо](real_data_demo_ru.md).
Ordinary false-color JPEG отклоняется, а не становится температурной матрицей.
Будущий converter должен работать отдельно и сохранять camera/file constants,
условия/corrections/provenance, затем публиковать этот decoded source profile.
[Thermimage raw2temp](https://search.r-project.org/CRAN/refmans/Thermimage/html/raw2temp.html)
— кандидат с GPL obligations, не runtime dependency текущего provider.

[Zenodo 10008937](https://zenodo.org/records/10008937), CC BY 4.0, содержит настоящий
FLIR Vue Pro R glacier payload; calibration/temperature conversion не проверены,
dataset не добавлен в Git и не объявляется skin/lumbar clinical корпусом.
Точное исследование hash/licensing — [report](../research/reuse/thermography/report_ru.md).

Mean/асимметрия температуры не устанавливают воспаление, локализацию источника
боли, наличие/отсутствие заболевания или степень клинической согласованности.
