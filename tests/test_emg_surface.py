from copy import deepcopy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from dorsopathy_modules.emg_surface import EMGSurfaceProvider
from dorsopathy_modules.emg_surface.provider import fft_median_frequency
from dorsopathy_platform.contracts import ContractError
from dorsopathy_platform.ports import ProviderContext
from dorsopathy_platform.storage import ArtifactStore

FIXTURES=Path(__file__).parent/'fixtures/emg_surface'


class EMGTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ArtifactStore(Path(self.directory.name));self.provider=EMGSurfaceProvider()
        self.data=json.loads((FIXTURES/'source.json').read_text());self.csv=(FIXTURES/'signal.csv').read_bytes()

    def execute(self,data=None,signal=None):
        data=deepcopy(data or self.data)
        csv_source=self.store.add_bytes(signal or self.csv,'text/csv');data['csv_artifact_id']=csv_source['artifact_id']
        source=self.store.add_bytes(json.dumps(data,allow_nan=False).encode(),'application/json')
        request={'schema_version':'1.0','job_id':'job-emg','episode_id':'episode-test','module_id':'emg.surface','module_version':'0.1.0',
                 'input_artifacts':[source,csv_source],'parameters':{},'protocol_id':'synthetic-static-v1'}
        return self.provider.analyze(request,ProviderContext(self.store))

    def mutate(self,index,column,value):
        rows=self.csv.decode().splitlines();parts=rows[index+1].split(',');parts[column]=str(value);rows[index+1]=','.join(parts)
        return ('\n'.join(rows)+'\n').encode()

    def test_known_periodic_signal_rms_and_frequency_match_oracle(self):
        result=self.execute()
        self.assertEqual(result['status'],'complete')
        for obs in result['observations']:
            self.assertAlmostEqual(obs['value'],math.sqrt(50) if obs['concept']=='emg.rms' else 125,places=8)
            self.assertEqual(obs['origin'],'synthetic_demo')
        self.assertEqual([obs['unit'] for obs in result['observations']],['uV','Hz','uV','Hz'])
        summary=next(json.loads(self.store.read(ref)) for ref in result['artifacts'] if ref['media_type']=='application/json' and json.loads(self.store.read(ref)).get('source_kind')=='emg.measurements.v1')
        self.assertEqual(summary['comparison_metadata']['acquired_interval']['end'],'2026-10-09T10:00:00.128000+00:00')
        self.assertEqual(summary['measurements'][0]['sample_stop_exclusive'],128)
        self.assertIsNone(summary['mvc_reference'])
        self.assertTrue(any(ref['media_type']=='image/png' for ref in result['artifacts']))

    def test_unknown_amplitude_scale_blocks_rms_without_inventing_frequency_unit(self):
        data=deepcopy(self.data);data['calibration'].update(status='unknown',uV_per_unit=None,source=None,author_id=None)
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['observations'][0]['value'])
        self.assertEqual(result['observations'][1]['value'],125)

    def test_body_region_is_preserved_and_muscle_stays_in_channel_evidence(self):
        result=self.execute()
        self.assertEqual([obs['anatomy']['region'] for obs in result['observations']],['lower_back']*4)
        self.assertEqual([obs['anatomy']['side'] for obs in result['observations']],['left','left','right','right'])
        summary=next(json.loads(self.store.read(ref)) for ref in result['artifacts'] if ref['media_type']=='application/json' and json.loads(self.store.read(ref)).get('source_kind')=='emg.measurements.v1')
        self.assertEqual([item['channel']['muscle'] for item in summary['measurements']],['erector_spinae_longissimus']*2)

    def test_unknown_sampling_rate_blocks_frequency_and_marks_window_quality_unknown(self):
        data=deepcopy(self.data);data['sample_rate_hz']=None
        result=self.execute(data)
        self.assertEqual(result['status'],'partial')
        self.assertAlmostEqual(result['observations'][0]['value'],math.sqrt(50))
        self.assertIsNone(result['observations'][1]['value'])
        self.assertEqual(result['observations'][0]['quality'],'unknown')

    def test_missing_sample_is_not_filled_and_other_channel_remains_valid(self):
        result=self.execute(signal=self.mutate(10,1,''))
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['observations'][0]['value'])
        self.assertIsNone(result['observations'][1]['value'])
        self.assertAlmostEqual(result['observations'][2]['value'],math.sqrt(50))

    def test_timestamp_gap_clipping_and_incomplete_window_are_explicit(self):
        result=self.execute(signal=self.mutate(127,0,'0.128'))
        self.assertTrue(all(obs['value'] is None for obs in result['observations']))
        data=deepcopy(self.data);data['adc_rails']={'lower':-9,'upper':9}
        result=self.execute(data)
        self.assertTrue(all(obs['quality']=='unreliable' and obs['value'] is None for obs in result['observations']))
        result=self.execute(signal=('\n'.join(self.csv.decode().splitlines()[:65])+'\n').encode())
        self.assertTrue(all(obs['value'] is None for obs in result['observations']))

    def test_explicit_mean_detrend_changes_offset_rms_without_hidden_filter(self):
        rows=self.csv.decode().splitlines()
        for index in range(1,len(rows)):
            fields=rows[index].split(',');fields[1]=str(float(fields[1])+20);fields[2]=str(float(fields[2])+20);rows[index]=','.join(fields)
        signal=('\n'.join(rows)+'\n').encode()
        result=self.execute(signal=signal)
        self.assertAlmostEqual(result['observations'][0]['value'],math.sqrt(450))
        self.assertEqual(result['observations'][1]['value'],0)
        data=deepcopy(self.data);data['processing']['detrend']='mean'
        result=self.execute(data,signal)
        self.assertAlmostEqual(result['observations'][0]['value'],math.sqrt(50))
        self.assertEqual(result['observations'][1]['value'],125)

    def test_zero_power_and_nyquist_frequency_are_distinct(self):
        frequency,_=fft_median_frequency([0]*128,1000);self.assertIsNone(frequency)
        frequency,_=fft_median_frequency([(-1)**index for index in range(128)],1000);self.assertEqual(frequency,500)
        zero=b'time_s,left_es,right_es\n'+b''.join(f'{index/1000},0,0\n'.encode() for index in range(128))
        result=self.execute(signal=zero)
        self.assertEqual(result['observations'][0]['value'],0)
        self.assertEqual(result['observations'][0]['quality'],'unreliable')
        self.assertIsNone(result['observations'][1]['value'])

    def test_bad_headers_nonmonotonic_times_or_unsupported_filter_rejected(self):
        for signal in (self.csv.replace(b'time_s,left_es,right_es',b'time_s,left_es,left_es'),self.mutate(10,0,0)):
            with self.assertRaises(ContractError):self.execute(signal=signal)
        data=deepcopy(self.data);data['processing']['filter']='bandpass'
        with self.assertRaises(ContractError):self.execute(data)


    def test_valid_large_input_cannot_expand_into_unbounded_overlapping_windows(self):
        data=deepcopy(self.data)
        data['channels']=[{'id':'c'+str(index),'column':'c'+str(index),'side':'left','muscle':'erector_spinae_longissimus','electrode_placement':None} for index in range(8)]
        data['processing'].update(window_samples=2048,hop_samples=1)
        header='time_s,'+','.join(channel['column'] for channel in data['channels'])+'\n'
        signal=(header+''.join(f'{index/1000:.3f},1,1,1,1,1,1,1,1\n' for index in range(32768))).encode()
        self.assertLess(len(signal),8*1024*1024)
        with self.assertRaises(ContractError):self.execute(data,signal)


if __name__=='__main__':unittest.main()
