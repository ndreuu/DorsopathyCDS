"""Bounded timestamped CSV and explicit RMS/discrete FFT median frequency."""
import cmath
import csv
from datetime import datetime, timedelta
import io
import math
from pathlib import Path
import tempfile
from dorsopathy_platform.api_contracts import opaque_id
from dorsopathy_platform.contracts import ContractError, validate_result
from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.results import result_envelope
from ._support import COMMON_FIELDS, MAX_SOURCE_BYTES, add_json, calibration, common, line, metadata, number, observation, png, read_source, require, text


def fft_median_frequency(values, sample_rate_hz):
    """One-sided squared FFT magnitude; first bin containing half total power."""
    size=len(values)
    if not 16<=size<=2048 or size&(size-1): raise ContractError('FFT requires a bounded power-of-two window')
    signal=[complex(value) for value in values]
    reversed_index=0
    for index in range(1,size):
        bit=size>>1
        while reversed_index&bit: reversed_index^=bit;bit>>=1
        reversed_index^=bit
        if index<reversed_index:signal[index],signal[reversed_index]=signal[reversed_index],signal[index]
    width=2
    while width<=size:
        increment=cmath.exp(-2j*math.pi/width)
        for start in range(0,size,width):
            factor=1+0j
            for offset in range(width//2):
                first=signal[start+offset];second=factor*signal[start+offset+width//2]
                signal[start+offset]=first+second;signal[start+offset+width//2]=first-second;factor*=increment
        width*=2
    power=[abs(signal[index])**2*(1 if index in (0,size//2) else 2) for index in range(size//2+1)]
    total=math.fsum(power)
    if total==0:return None,power
    accumulated=0
    for index,value in enumerate(power):
        accumulated+=value
        if accumulated>=total/2:return index*sample_rate_hz/size,power
    return sample_rate_hz/2,power


def _timestamp(stamp,seconds):
    if stamp is None or seconds is None:return None
    parsed=datetime.fromisoformat(stamp[:-1]+'+00:00' if stamp.endswith('Z') else stamp)
    return (parsed+timedelta(seconds=seconds)).isoformat()


def _csv(source,context,time_column,channels):
    if source['size_bytes']>MAX_SOURCE_BYTES:raise ContractError('EMG CSV exceeds bounded profile')
    rows=[];previous=None
    try:
        with context.artifacts.open(source) as binary,io.TextIOWrapper(binary,encoding='utf-8',newline='') as stream:
            reader=csv.DictReader(stream)
            expected=[time_column]+[item['column'] for item in channels]
            if reader.fieldnames is None or len(reader.fieldnames)!=len(set(reader.fieldnames)) or set(reader.fieldnames)!=set(expected):
                raise ContractError('EMG CSV headers must match declared time and channels')
            for raw in reader:
                if None in raw or any(value is None for value in raw.values()):raise ContractError('EMG CSV rows have inconsistent columns')
                def parse(value):
                    if value.strip() in {'','NA','NaN','nan'}:return None
                    return number(float(value))
                time=parse(raw[time_column])
                if time is not None:
                    if time<0 or previous is not None and time<=previous:raise ContractError('EMG sample times must be increasing and nonnegative')
                    previous=time
                rows.append((time,[parse(raw[item['column']]) for item in channels]))
                if len(rows)>32768:raise ContractError('EMG CSV sample count exceeds bounded profile')
    except (UnicodeDecodeError,ValueError,csv.Error):
        raise ContractError('EMG CSV contains invalid numeric or text data') from None
    if not rows:raise ContractError('EMG CSV contains no samples')
    return rows


class EMGSurfaceProvider:
    spec=ProviderSpec('emg.surface','0.1.0','Timestamped surface EMG and explicit window features')

    def analyze(self,request,context):
        request,data,source=read_source(request,context,self.spec)
        require(data,COMMON_FIELDS|{'csv_artifact_id','time_column','sample_rate_hz','channels','adc_rails','processing'})
        common(data,'emg-csv.v1',1);cal=calibration(data,{'uV_per_unit'})
        factor=None if cal['uV_per_unit'] is None else number(cal['uV_per_unit'],positive=True)
        known_units=cal['status']=='verified' and factor is not None
        if cal['status']=='verified' and not known_units:raise ContractError('Known EMG units require declared microvolt scale')
        rate=None if data['sample_rate_hz'] is None else number(data['sample_rate_hz'],positive=True)
        opaque_id(data['time_column'])
        channels=data['channels']
        if type(channels) is not list or not 1<=len(channels)<=8:raise ContractError('EMG requires one to eight channels')
        ids,columns=set(),set()
        for item in channels:
            require(item,{'id','column','side','muscle','electrode_placement'});opaque_id(item['id']);opaque_id(item['column']);text(item['muscle'])
            if item['id'] in ids or item['column'] in columns or item['side'] not in {'left','right','midline','bilateral','unspecified'} or item['column']==data['time_column']:
                raise ContractError('EMG channel identity, column or side is invalid')
            if item['electrode_placement'] is not None:text(item['electrode_placement'])
            ids.add(item['id']);columns.add(item['column'])
        rails=data['adc_rails']
        if rails is not None:
            require(rails,{'lower','upper'});lower,upper=number(rails['lower']),number(rails['upper'])
            if lower>=upper:raise ContractError('ADC rails require a valid source-unit interval')
        processing=require(data['processing'],{'filter','detrend','spectral_window','window_samples','hop_samples','timing_tolerance_fraction'})
        window,hop=processing['window_samples'],processing['hop_samples']
        if type(window) is not int or not 16<=window<=2048 or window&(window-1) or type(hop) is not int or not 1<=hop<=window or processing['filter']!='none' or processing['detrend'] not in {'none','mean'} or processing['spectral_window']!='rectangular':
            raise ContractError('EMG processing profile requires explicit supported windows and no filter')
        tolerance=number(processing['timing_tolerance_fraction'])
        if not 0<=tolerance<0.5:raise ContractError('EMG timing tolerance is unsupported')
        references={ref['artifact_id']:ref for ref in request['input_artifacts']};csv_source=references.get(data['csv_artifact_id'])
        if len(references)!=2 or csv_source is None or csv_source['role']!='source' or csv_source['media_type'] not in {'text/csv','application/csv'}:
            raise ContractError('EMG requires its declared raw CSV artifact')
        rows=_csv(csv_source,context,data['time_column'],channels)
        starts=list(range(0,max(1,len(rows)-window+1),hop))
        last=starts[-1]+window
        if last<len(rows):starts.append(starts[-1]+hop)
        if len(starts)*len(channels)>2048 or len(starts)*len(channels)*window>524288:
            raise ContractError('EMG window schedule exceeds bounded processing profile')
        measures=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'processed.csv'
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['window_id','channel_id','sample_index','time_s','processed_value','unit'])
                for start in starts:
                    segment=rows[start:start+window];times=[row[0] for row in segment]
                    for channel_index,channel in enumerate(channels):
                        values=[row[1][channel_index] for row in segment]
                        reasons=[];quality='usable'
                        if len(segment)!=window:reasons.append('incomplete_window')
                        if any(value is None for value in values):reasons.append('missing_signal_sample')
                        if any(time is None for time in times):reasons.append('missing_timestamp')
                        if rate is not None and all(time is not None for time in times) and any(abs((b-a)-1/rate)>tolerance/rate for a,b in zip(times,times[1:])):reasons.append('sample_gap_or_rate_conflict')
                        if rails is not None and any(value is not None and (value<=lower or value>=upper) for value in values):reasons.append('clipping')
                        if reasons:quality='unreliable'
                        elif rails is None or rate is None:quality='unknown'
                        processed=[];rms,frequency,power=None,None,[]
                        if not reasons:
                            offset=math.fsum(values)/len(values) if processing['detrend']=='mean' else 0
                            processed=[(value-offset)*(factor if known_units else 1) for value in values]
                            if known_units:rms=math.sqrt(math.fsum(value*value for value in processed)/len(processed))
                            if rate is not None:
                                frequency,power=fft_median_frequency(processed,rate)
                                if frequency is None:quality='unreliable';reasons.append('zero_spectral_power')
                        identifier=channel['id']+'-'+str(start)
                        for index,value in enumerate(processed):writer.writerow([identifier,channel['id'],start+index,times[index],value,'uV' if known_units else 'source_units'])
                        measures.append({'id':identifier,'channel':channel,'sample_start':start,'sample_stop_exclusive':start+len(segment),
                                         'start_s':times[0] if times else None,'end_s':times[-1]+1/rate if times and times[-1] is not None and rate is not None else None,
                                         'rms_uV':rms,'median_frequency_Hz':frequency,'quality':quality,'qc_reasons':reasons,
                                         'amplitude_scale_known':known_units,'clipping_verifiable':rails is not None,
                                         'spectrum_power':power,'spectrum_power_unit':'uV2' if known_units else 'source_units_squared',
                                         'spectral_bin_hz':rate/window if rate is not None else None})
            processed_ref=context.artifacts.add_file(path,'text/csv',role='derived')
        previews=self._plot(context,rows,channels)
        comparison=metadata(data,source,None,cal['status'] if known_units else 'invalid' if cal['status']=='invalid' else 'unknown')
        comparison['acquired_interval']={'start':_timestamp(data['acquired_at'],rows[0][0]),'end':_timestamp(data['acquired_at'],rows[-1][0]+1/rate if rows[-1][0] is not None and rate is not None else None),'clock_domain':data['clock_domain']}
        summary={'schema_version':'1.0','source_kind':'emg.measurements.v1','source_artifact_id':source['artifact_id'],'raw_csv_artifact_id':csv_source['artifact_id'],
                 'processed_artifact_id':processed_ref['artifact_id'],'sample_rate_hz':rate,'processing':processing,'calibration':cal,
                 'mvc_reference':None,'measurements':measures,'comparison_metadata':comparison}
        measurement_ref=add_json(context,summary);observations=[]
        for item in measures:
            local={**data,'anatomy':{**data['anatomy'],'region':item['channel']['muscle'],'side':item['channel']['side']}}
            evidence=[{'artifact_id':csv_source['artifact_id'],'selector':{'kind':'sample_interval','channel_id':item['channel']['id'],'sample_start':item['sample_start'],'sample_stop_exclusive':item['sample_stop_exclusive']}},
                      {'artifact_id':measurement_ref['artifact_id'],'selector':{'kind':'measurement','measurement_id':item['id'],'frame_index':0}},
                      {'artifact_id':previews[1]['artifact_id'],'selector':{'kind':'image','frame_index':0,'coordinate_frame':None}}]
            for concept,value,unit in [('emg.rms',item['rms_uV'],'uV'),('emg.median_frequency',item['median_frequency_Hz'],'Hz')]:
                quality=item['quality'] if value is not None else 'unreliable' if item['quality']=='unreliable' or cal['status']=='invalid' else 'unknown'
                observations.append(observation(request,local,concept,item['id'],value,unit,quality,'window_'+processing['detrend']+'_detrend_'+('rms_v1' if concept=='emg.rms' else 'rectangular_fft_discrete_median_v1'),evidence))
        partial=any(obs['value'] is None or obs['quality']!='usable' for obs in observations)
        warnings=[{'code':'emg_measurement_incomplete','message':'Some windows, scales, sampling or signal quality are unknown; surface features do not diagnose radiculopathy.'}] if partial else []
        return validate_result(result_envelope(request,observations,[source,csv_source,processed_ref,*previews,measurement_ref],'partial' if partial else 'complete',warnings),request)

    @staticmethod
    def _plot(context,rows,channels):
        width,height=640,240;image=[bytearray([15,20,30]*width) for _ in range(height)]
        svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="640" height="240" fill="#0f141e"/>']
        finite=[value for _,values in rows for value in values if value is not None];maximum=max([abs(value) for value in finite]+[1])
        for channel_index,channel in enumerate(channels):
            previous=None;points=[];color=(0,255,255) if channel_index%2==0 else (255,200,0)
            for index,(time,values) in enumerate(rows):
                value=values[channel_index]
                if value is None:previous=None;points.append(None);continue
                point=[20+600*index/max(1,len(rows)-1),120-90*value/maximum]
                if previous is not None:line(image,previous,point,color)
                previous=point;points.append(point)
            segments=[];segment=[]
            for point in points+[None]:
                if point is None:
                    if segment:segments.append(segment);segment=[]
                else:segment.append(point)
            for segment in segments:svg.append('<polyline points="'+' '.join(f'{p[0]:.3f},{p[1]:.3f}' for p in segment)+'" fill="none" stroke="'+('#00ffff' if channel_index%2==0 else '#ffc800')+'"/>')
        svg.append('<text x="20" y="225" fill="white">Raw source amplitude versus sample index; no interpolation across missing values</text></svg>')
        return [context.artifacts.add_bytes(''.join(svg).encode(),'image/svg+xml',role='derived'),context.artifacts.add_bytes(png(width,height,image),'image/png',role='derived')]
