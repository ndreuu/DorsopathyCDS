#!/usr/bin/env python3
"""Build a portable, entirely synthetic eight-module patient ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dorsopathy_platform.api_contracts import canonical_bytes
from dorsopathy_platform.synthetic_bundle import inspect_bundle
from synthetic_patient import clinical, imaging, signals

README = '''# SYNTHETIC-PATIENT-001 — учебный пациент

Все данные созданы программно. Реальных записей пациента в архиве нет.
Случай: сильная боль в пояснице, распространение в ногу, новое онемение промежности.
Цель: проверить сырой импорт, измерения, QC, сохранение неизвестных данных и
цитируемое правило направления. Условия NICE должны выполниться; это не диагноз.

В case.json перечислены модули, файлы, SHA256, версия формата и единая учебная
идентичность. Жалобы/осмотр — выдуманные явные ответы. МРТ — геометрический
source-grid фантом с заданной маской канала; neural MRI inference НЕ запускается.
ЭМГ — синусы с известной амплитудой/частотой; depth — геометрическая плоскость;
тепловизор — новый радиометрический FLIR JPEG с заданным полем температур;
УЗИ — процедурный PNG без заданной маски, обрабатываемый настоящей UltraSAM3;
PMD-200 — выдуманный документированный CSV. УЗИ outside model distribution:
отсутствие или непригодность найденной маски — допустимый результат. Нормальные
показатели датчиков не исключают болезнь. Связь координат модулей не подтверждена.

Запуск из checkout DorsopathyCDS (веса/окружения настраиваются отдельно):

    PYTHONPATH=src python3 scripts/run_synthetic_patient.py \\
      --archive var/bundles/synthetic-patient-001.zip \\
      --raw-workers-config var/raw-workers.json \\
      --store var/synthetic-patient-demo --output var/synthetic-patient-run

Runner проверяет архив и загружает все входы одного episode, без extractall и
команд из архива. Результат — run, snapshot, summary, report.json и report.html.
Для браузера после завершения CLI:

    PYTHONPATH=src python3 -m dorsopathy_platform serve \\
      --store var/synthetic-patient-demo --port 8769 \\
      --raw-workers-config var/raw-workers.json

URL готового run указан в summary.json. Подробнее: docs/synthetic_patient_ru.md.
'''


def build(destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='dorsopathy-synthetic-build-') as temporary:
        root = Path(temporary)
        modules = clinical.generate(root) + signals.generate(root) + imaging.generate(root, metadata={'clock_domain':'synthetic-clock'})
        # Each source schema remains unchanged; metadata binds the synthetic
        # inputs, without claiming common anatomical coordinates or real clocks.
        mri_path = root/'inputs/mri/source.json'
        source = json.loads(mri_path.read_text())
        source['comparison_metadata']['acquired_interval']['clock_domain'] = 'synthetic-clock'
        mri_path.write_bytes(canonical_bytes(source))
        (root/'README.md').write_text(README, encoding='utf-8')
        files = []
        media = {'.json':'application/json','.png':'image/png','.jpg':'image/jpeg',
                 '.csv':'text/csv','.md':'text/markdown','.xml':'application/xml'}
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            content = path.read_bytes()
            files.append({'path':path.relative_to(root).as_posix(),'media_type':media[path.suffix],
                          'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content)})
        case = {'schema_version':'1.0','source_kind':'synthetic.patient-bundle.v1',
                'case_id':'synthetic-patient-001','origin':'synthetic_demo',
                'subject_ref':clinical.SUBJECT,'session_ref':clinical.SESSION,
                'protocol_id':clinical.PROTOCOL,'acquired_at':clinical.TIME,
                'title':'Учебный пациент: сырой ввод восьми модулей и проверка направления',
                'modules':modules,'files':files}
        (root/'case.json').write_bytes(canonical_bytes(case))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for path in sorted(p for p in root.rglob('*') if p.is_file()):
                info = zipfile.ZipInfo(path.relative_to(root).as_posix(),(2026,1,1,12,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info,path.read_bytes(),compresslevel=9)
        with zipfile.ZipFile(destination) as archive:inspect_bundle(archive)
    sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix+'.sha256').write_text(sha+'  '+destination.name+'\n')
    return {'archive':str(destination),'sha256':sha,'bytes':destination.stat().st_size,'modules':len(modules),'origin':'synthetic_demo'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('var/bundles/synthetic-patient-001.zip'))
    args = parser.parse_args()
    print(json.dumps(build(args.output),ensure_ascii=False))
