"""Canonical recorded index import; no proprietary ANI/NOL computation or driver."""
from datetime import datetime,timedelta
import html
import math
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError,validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope
from ._support import COMMON_FIELDS,add_json,calibration,common,line,metadata,number,observation,png,read_source,require,text


def _timestamp(stamp,seconds):
    if stamp is None or seconds is None:return None
    parsed=datetime.fromisoformat(stamp[:-1]+'+00:00' if stamp.endswith('Z') else stamp)
    return (parsed+timedelta(seconds=seconds)).isoformat()


class NociceptionProvider:
    spec=ProviderSpec('nociception','0.1.0','Canonical timestamped device index import')

    def analyze(self,request,context):
        request,data,source=read_source(request,context,self.spec)
        require(data,COMMON_FIELDS|{'device','state','unit','sampling_period_s','timing_tolerance_fraction','rows','events','selection'})
        common(data,'nociception-index.v1',1);cal=calibration(data)
        if len(request['input_artifacts'])!=1:raise ContractError('Device-index profile requires one canonical JSON source')
        device=require(data['device'],{'manufacturer','model','software_version','index_field','averaging_window_s'})
        for field in ('manufacturer','model','software_version','index_field'):
            if device[field] is not None:text(device[field])
        averaging=None if device['averaging_window_s'] is None else number(device['averaging_window_s'],positive=True)
        period=None if data['sampling_period_s'] is None else number(data['sampling_period_s'],positive=True)
        tolerance=number(data['timing_tolerance_fraction'])
        if not 0<=tolerance<0.5 or data['unit'] not in ('1',None) or data['state'] not in {'synthetic','awake','sedated','general_anesthesia','unknown'}:
            raise ContractError('Device index unit, state or timing tolerance is unsupported')
        if data['acquisition_mode']=='synthetic' and data['state']!='synthetic' or data['acquisition_mode']=='recorded' and data['state']=='synthetic':
            raise ContractError('Device source state cannot contradict acquisition origin')
        rows=data['rows'];events=data['events']
        if type(rows) is not list or not 1<=len(rows)<=32768 or type(events) is not list:
            raise ContractError('Device index rows/events exceed the canonical profile')
        event_map={}
        for event in events:
            require(event,{'id','label','time_s'});opaque_id(event['id']);text(event['label'])
            if event['id'] in event_map or number(event['time_s'])<0:raise ContractError('Device event identity/time is invalid')
            event_map[event['id']]=event
        previous=None;missing_time=False
        for row in rows:
            require(row,{'time_s','value','quality','events'})
            if row['time_s'] is None:missing_time=True
            else:
                time=number(row['time_s'])
                if time<0 or previous is not None and time<=previous:raise ContractError('Index times must be increasing and nonnegative')
                previous=time
            if row['value'] is not None and not 0<=number(row['value'])<=100:raise ContractError('Canonical device index must stay in its 0 to 100 scale')
            if row['quality'] not in {'usable','unreliable','unknown'} or type(row['events']) is not list:
                raise ContractError('Canonical device quality/events are invalid')
            for event_id in row['events']:
                opaque_id(event_id)
                if event_id not in event_map:raise ContractError('Device row refers to undeclared event')
            if len(row['events'])!=len(set(row['events'])):raise ContractError('Device row events must be unique')
        selection=require(data['selection'],{'window_id','start_s','end_s'});opaque_id(selection['window_id'])
        start,stop=number(selection['start_s']),number(selection['end_s'])
        if start<0 or stop<=start:raise ContractError('Selected index window must be positive and half-open')
        selected=[(index,row) for index,row in enumerate(rows) if row['time_s'] is not None and start<=row['time_s']<stop]
        reasons=[]
        if missing_time:reasons.append('unassignable_timestamp')
        if not selected:reasons.append('empty_window')
        if data['unit']!='1' or cal['status']!='verified':reasons.append('unknown_or_invalid_scale')
        if device['index_field'] is None or averaging is None:reasons.append('unknown_index_definition_or_averaging_window')
        if period is None:reasons.append('unknown_sampling_period')
        if selected:
            if any(row['value'] is None for _,row in selected):reasons.append('missing_index')
            if any(row['quality']!='usable' for _,row in selected):reasons.append('window_quality_not_usable')
            if period is not None:
                selected_times=[row['time_s'] for _,row in selected]
                if selected_times[0]-start>period*tolerance or stop-(selected_times[-1]+period)>period*tolerance or any(abs((b-a)-period)>period*tolerance for a,b in zip(selected_times,selected_times[1:])):
                    reasons.append('window_gap_or_incomplete_coverage')
        value=math.fsum(row['value'] for _,row in selected)/len(selected) if not reasons else None
        quality='unreliable' if any(reason in reasons for reason in ('missing_index','window_quality_not_usable','window_gap_or_incomplete_coverage','unassignable_timestamp')) or cal['status']=='invalid' else 'unknown' if reasons or data['acquired_at'] is None or data['clock_domain'] is None else 'usable'
        previews=self._plot(context,rows,events,period,tolerance,data['unit'])
        comparison=metadata(data,source,None,cal['status'])
        comparison['acquired_interval']={'start':_timestamp(data['acquired_at'],start),'end':_timestamp(data['acquired_at'],stop),'clock_domain':data['clock_domain']}
        matching_events=[event['id'] for event in events if start<=event['time_s']<stop]
        comparison['event_id']=matching_events[0] if len(matching_events)==1 else None
        summary={'schema_version':'1.0','source_kind':'nociception.measurements.v1','source_artifact_id':source['artifact_id'],
                 'device':device,'state':data['state'],'unit':data['unit'],'selection':selection,'selected_row_indices':[index for index,_ in selected],
                 'value':value,'quality':quality,'qc_reasons':reasons,'method':'unweighted_mean_of_selected_device_index_samples_v1',
                 'events':events,'comparison_metadata':comparison}
        measurement_ref=add_json(context,summary)
        evidence=[{'artifact_id':source['artifact_id'],'selector':{'kind':'json','pointer':'/rows','row_indices':[index for index,_ in selected], 'start_s':start,'end_s_exclusive':stop}},
                  {'artifact_id':measurement_ref['artifact_id'],'selector':{'kind':'measurement','measurement_id':selection['window_id'],'frame_index':0}},
                  {'artifact_id':previews[1]['artifact_id'],'selector':{'kind':'image','frame_index':0,'coordinate_frame':None}}]
        obs=observation(request,data,'nociception.device_index',selection['window_id'],value,'1',quality,'unweighted_selected_device_index_mean_v1',evidence)
        partial=value is None or quality!='usable'
        warnings=[{'code':'device_index_incomplete','message':'The selected index window, quality, timing or definition is unknown; no pain interpretation was produced.'}] if partial else []
        return validate_result(result_envelope(request,[obs],[source,*previews,measurement_ref],'partial' if partial else 'complete',warnings),request)

    @staticmethod
    def _plot(context,rows,events,period,tolerance,unit):
        width,height=640,240;image=[bytearray([15,20,30]*width) for _ in range(height)]
        valid=[row for row in rows if row['time_s'] is not None];start=min([row['time_s'] for row in valid]+[0]);stop=max([row['time_s'] for row in valid]+[1]);span=max(stop-start,1e-12)
        svg=['<svg xmlns="http://www.w3.org/2000/svg" width="640" height="240" viewBox="0 0 640 240"><rect width="640" height="240" fill="#0f141e"/>']
        previous=None;previous_time=None
        for row in rows:
            if row['time_s'] is None or row['value'] is None:previous=None;previous_time=None;continue
            if previous_time is not None and period is not None and abs((row['time_s']-previous_time)-period)>period*tolerance:previous=None
            point=[20+600*(row['time_s']-start)/span,210-1.8*row['value']]
            color=(0,255,255) if row['quality']=='usable' else (255,80,80)
            if previous is not None:
                line(image,previous,point,color)
                svg.append(f'<line x1="{previous[0]:.3f}" y1="{previous[1]:.3f}" x2="{point[0]:.3f}" y2="{point[1]:.3f}" stroke="'+('#00ffff' if row['quality']=='usable' else '#ff5050')+'"/>')
            previous=point;previous_time=row['time_s']
        for event in events:
            if start<=event['time_s']<=stop:
                x=20+600*(event['time_s']-start)/span;line(image,[x,20],[x,210],(255,200,0))
                svg.append(f'<line x1="{x:.3f}" y1="20" x2="{x:.3f}" y2="210" stroke="#ffc800"/><text x="{x:.3f}" y="15" fill="white">{html.escape(event["label"])}</text>')
        svg.append(f'<text x="20" y="230" fill="white">Recorded source index [0,100], unit {unit if unit is not None else "unknown"}; time_s {start:.4g} to {stop:.4g}</text></svg>')
        return [context.artifacts.add_bytes(''.join(svg).encode(),'image/svg+xml',role='derived'),context.artifacts.add_bytes(png(width,height,image),'image/png',role='derived')]
