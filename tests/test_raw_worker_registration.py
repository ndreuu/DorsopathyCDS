"""Trusted process configuration is opt-in and cannot override legacy providers."""
import json
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import patch
from dorsopathy_platform.bootstrap import build_registry, create_application
from dorsopathy_platform.results import result_envelope
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.registry import RegistryError


class RawWorkerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=self.root/'workers.json'

    def test_workers_registered_only_when_configured_and_legacy_preserved(self):
        self.config.write_text(json.dumps({'emg.surface':{'command':['/trusted/python','-m','worker'],'timeout_seconds':120}}))
        with patch('subprocess.Popen') as process:
            registry=build_registry(self.root/'store',raw_workers_config=self.config)
            process.assert_not_called()
        provider=registry.get('emg.surface','0.2.0');self.addCleanup(provider.close)
        self.assertIsInstance(provider,ProcessProvider)
        self.assertEqual(provider.command,('/trusted/python','-m','worker'))
        self.assertNotIsInstance(registry.get('emg.surface','0.1.0'),ProcessProvider)
        self.assertEqual(registry.get('complaints','0.2.0').spec.module_id,'complaints')
        with self.assertRaises(RegistryError):registry.get('ultrasound','0.2.0')

    def test_memory_heavy_ultrasound_serializes_with_mri_but_legacy_us_and_cpu_continue(self):
        self.config.write_text(json.dumps({'ultrasound':{'command':['/trusted/python'],'timeout_seconds':120}}))
        app=create_application(self.root/'store',max_workers=1,raw_workers_config=self.config)
        entered=threading.Event();release=threading.Event();ultrasound_entered=threading.Event()
        def mri(request,context):
            entered.set();release.wait(3);return result_envelope(request,[],[])
        def ultrasound(request,context):
            ultrasound_entered.set();return result_envelope(request,[],[])
        app.registry.get('mri.anatomy','0.1.0').analyze=mri
        app.registry.get('ultrasound','0.2.0').analyze=ultrasound
        app.registry.get('ultrasound','0.1.0').analyze=lambda r,c:result_envelope(r,[],[])
        try:
            episode=app.create_episode()['episode_id']
            for module,version,job in [('mri.anatomy','0.1.0','mri'),('ultrasound','0.2.0','us-heavy'),('ultrasound','0.1.0','us-legacy')]:
                app.repository.create_job({'schema_version':'1.0','job_id':job,'episode_id':episode,
                    'module_id':module,'module_version':version,'input_artifacts':[],'parameters':{},'protocol_id':None})
            first=app.dispatcher.enqueue('mri');self.assertTrue(entered.wait(2))
            heavy=app.dispatcher.enqueue('us-heavy');legacy=app.dispatcher.enqueue('us-legacy')
            self.assertEqual(legacy.result(2)['status'],'complete');self.assertFalse(ultrasound_entered.is_set())
            self.assertEqual(app.repository.get_job('us-heavy')['status'],'queued')
            release.set();self.assertEqual(first.result(3)['status'],'complete')
            self.assertEqual(heavy.result(3)['status'],'complete');self.assertTrue(ultrasound_entered.is_set())
        finally:release.set();app.close()

    def test_untrusted_or_malformed_configuration_fails_before_process_start(self):
        bad=[{},[],{'untrusted.module':{'command':['python'],'timeout_seconds':1}},
             {'emg.surface':{'command':'python w.py','timeout_seconds':1}},
             {'emg.surface':{'command':['python'],'timeout_seconds':False}},
             {'emg.surface':{'command':['python'],'timeout_seconds':10**400}},
             {'emg.surface':{'command':['python'],'timeout_seconds':1,'shell':True}}]
        for config in bad:
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                with patch('subprocess.Popen') as process,self.assertRaises(ValueError):
                    build_registry(self.root/'store',raw_workers_config=self.config)
                process.assert_not_called()

    def test_oversized_or_duplicate_config_rejected(self):
        for text in (' '*65537,'{"emg.surface":{},"emg.surface":{}}'):
            self.config.write_text(text)
            with self.assertRaises(ValueError):build_registry(self.root/'store',raw_workers_config=self.config)


if __name__=='__main__':unittest.main()
