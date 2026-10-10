"""Trusted local catalog of separate public cases; never patient linkage evidence."""
import html
from pathlib import Path
from urllib.parse import urlsplit
from .api_contracts import opaque_id
from .contracts import ContractError, loads_json

LABELS = {
 'spine.central_stenosis.grade':'Центральный стеноз: категория модели',
 'spine.disc.herniation.present':'Грыжа: категория модели',
 'spine.canal.cross_sectional_area':'Площадь канала',
 'spine.canal.ap_extent':'AP-размер канала',
 'spine.disc.mask_volume':'Объём маски диска',
 'posture.landmark_distance':'Расстояние ориентиров',
 'posture.landmark_angle':'Угол ориентиров',
 'thermal.roi_mean_temperature':'Температура ROI',
 'thermal.left_right_delta':'ΔT слева − справа',
 'emg.rms':'RMS', 'emg.median_frequency':'Медианная частота',
 'ultrasound.distance':'Длина линии', 'ultrasound.area':'Площадь ROI',
 'nociception.device_index':'Индекс прибора',
}
UNITS = {'mm2':'мм²','mm3':'мм³','mm':'мм','Hz':'Гц','Cel':'°C','deg':'°','1':''}

def read_catalog(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('Example catalog is unavailable')
    data=loads_json(path.read_bytes())
    if type(data) is not dict or set(data)!={'schema_version','examples','unavailable'} or data['schema_version']!='1.0':
        raise ValueError('Invalid example catalog')
    if type(data['examples']) is not list or len(data['examples'])>20 or type(data['unavailable']) is not list or len(data['unavailable'])>20:
        raise ValueError('Example catalog exceeds limit')
    seen=set()
    for item in data['examples']:
        required={'id','title','description','module_id','episode_id','run_id','source','notes','preview_artifact_ids'}
        if type(item) is not dict or set(item)!=required:
            raise ValueError('Invalid example item')
        for key in ('id','module_id','episode_id','run_id'):
            opaque_id(item[key])
        if item['id'] in seen:raise ValueError('Duplicate example')
        seen.add(item['id'])
        source=item['source']
        if type(source) is not dict or set(source)!={'title','url','license'}:
            raise ValueError('Invalid source attribution')
        if type(source['url']) is not str:raise ValueError('Invalid source URL')
        if type(item['notes']) is not list or len(item['notes'])>20:raise ValueError('Invalid notes')
        url=urlsplit(source['url'])
        if url.scheme!='https' or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError('Source must be a public HTTPS URL')
        for value in (item['title'],item['description'],*source.values(),*item['notes']):
            if type(value) is not str or len(value)>4000:raise ValueError('Invalid catalog text')
        if type(item['notes']) is not list or len(item['notes'])>20 or type(item['preview_artifact_ids']) is not list or len(item['preview_artifact_ids'])>6:
            raise ValueError('Invalid preview selection')
        for aid in item['preview_artifact_ids']:opaque_id(aid)
    for item in data['unavailable']:
        if type(item) is not dict or set(item)!={'title','reason'} or any(type(v) is not str or len(v)>4000 for v in item.values()):
            raise ValueError('Invalid unavailable item')
    return data

def render_catalog(data,application):
    esc=lambda value:html.escape(str(value),quote=True)
    cards=[]
    for item in data['examples']:
        run=application.get_run(item['run_id'])
        if run['episode_id']!=item['episode_id'] or run['manifest']['linkage']!='unlinked':
            raise ValueError('Public examples must be isolated unlinked cases')
        result=next((r for r in application.get_snapshot(run['snapshot_id'])['results'] if r['module_id']==item['module_id']),None)
        if result is None:raise ValueError('Catalog result is unavailable')
        if any(obs['origin']=='synthetic_demo' for obs in result['observations']):
            raise ValueError('Synthetic observations are not real examples')
        base='/episodes/'+item['episode_id']+'/artifacts/'
        artifacts={a['artifact_id']:a for a in result['artifacts']}
        images=[]
        for aid in item['preview_artifact_ids']:
            ref=artifacts.get(aid)
            if ref is None or ref['media_type'] not in {'image/png','image/jpeg'}:
                raise ValueError('Preview is not a result image')
            images.append('<a href="'+base+aid+'" target="_blank"><img src="'+base+aid+'" alt="'+esc(item['title'])+'"></a>')
        rows=[]; unknown=0
        for obs in result['observations']:
            if obs['assessment_status']!='assessed':unknown+=1;continue
            if len(rows)>=8:continue
            anatomy=obs['anatomy'];where=' · '.join(str(anatomy[k]) for k in ('level','side') if anatomy.get(k) and anatomy[k]!='not_applicable')
            value=obs['value'];value=({'none':'Нет компрессии','mild':'Лёгкая','moderate':'Умеренная','severe':'Тяжёлая'}.get(value,value) if obs['concept']=='spine.central_stenosis.grade' else ('Да' if value else 'Нет') if type(value) is bool else value);value=(format(value,'.3f').rstrip('0').rstrip('.').replace('.',',') if type(value) in (int,float) else str(value))
            rows.append('<tr><td>'+esc(LABELS.get(obs['concept'],obs['concept']))+'<small>'+esc(where)+'</small></td><td>'+esc(value)+' '+esc(UNITS.get(obs['unit'],obs['unit'] or ''))+'</td></tr>')
        source=item['source']
        cards.append('<article><div class="eyebrow">РЕАЛЬНЫЙ ВХОД · '+esc(item['module_id'])+'</div><h2>'+esc(item['title'])+'</h2><p>'+esc(item['description'])+'</p><div class="gallery">'+''.join(images)+'</div><table>'+''.join(rows)+'</table><p class="muted">Не оценено: '+str(unknown)+' наблюдений. Результат: '+esc(result['status'])+'.</p><ul>'+''.join('<li>'+esc(note)+'</li>' for note in item['notes'])+'</ul><div class="actions"><a class="button" href="/#run='+item['run_id']+'">Снимки, все измерения и JSON →</a><a href="'+esc(source['url'])+'" target="_blank" rel="noopener noreferrer">'+esc(source['title'])+'</a><small>'+esc(source['license'])+'</small></div></article>')
    unavailable=''.join('<article class="pending"><h2>'+esc(item['title'])+'</h2><p>'+esc(item['reason'])+'</p></article>' for item in data['unavailable'])
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Настоящие данные — DorsopathyCDS</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#18253b;font:16px/1.55 -apple-system,BlinkMacSystemFont,Arial,sans-serif}main{max-width:1400px;margin:auto;padding:38px 24px}header{max-width:940px;margin-bottom:28px}h1{font-size:38px;line-height:1.15;margin:8px 0 20px}h2{font-size:24px;margin:8px 0}p{margin:10px 0}.eyebrow{font-size:12px;letter-spacing:1px;font-weight:700;color:#376ca5}.notice{background:#e3edf8;padding:16px 20px;border-radius:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(390px,1fr));gap:24px}article{background:white;border:1px solid #dce4ee;border-radius:16px;padding:24px;overflow:hidden}.gallery{display:flex;gap:8px;margin:20px 0;align-items:center;background:#121c2c;border-radius:10px;overflow:hidden;min-height:180px}.gallery a{flex:1;min-width:0}.gallery img{width:100%;max-height:360px;object-fit:contain;display:block}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td{padding:8px 0;border-bottom:1px solid #edf0f5}td:last-child{text-align:right;font-weight:600;white-space:nowrap}small{display:block;font-size:12px;color:#66738a}.muted,li{color:#56657b;font-size:14px}ul{padding-left:18px}.actions{display:grid;gap:10px;margin-top:20px}a{color:#215fc1}.button{display:block;text-decoration:none;background:#eaf1fd;border-radius:8px;padding:10px 14px;font-weight:600}.pending{margin-top:24px;background:#eaf0f7}@media(max-width:500px){main{padding:24px 14px}.grid{display:block}article{margin-bottom:20px;padding:18px}h1{font-size:30px}}
</style><main><header><div class="eyebrow">DORSOPATHY CDS · ПУБЛИЧНЫЕ ДАННЫЕ</div><h1>Настоящие входы.<br>Проверяемые измерения.</h1><p>Открытые записи, изображения и 3D-скан прошли через рабочие модули. Каждый пример — отдельный случай со своим исходником, результатом и изображениями.</p><p class="notice">Это разные люди из разных наборов. Они не объединены в одного пациента. Здесь демонстрируются импорт, измерения и контроль качества; клинический диагноз по этим примерам не формируется.</p></header><section class="grid">'''+''.join(cards)+'</section>'+unavailable+'</main></html>'
    return page.encode('utf-8')
