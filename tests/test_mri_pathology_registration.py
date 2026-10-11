"""Opt-in isolated MRI registration, without executing a clinical model."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import MagicMock, patch

from dorsopathy_platform.bootstrap import build_registry, create_application
from dorsopathy_platform.cli import main
from dorsopathy_platform.process_provider import ProcessProvider
from dorsopathy_platform.registry import RegistryError
from dorsopathy_platform.results import result_envelope


class MRIPathologyRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def config(self, name='pathology.json', command=None, timeout=600):
        path = self.root / name
        path.write_text(json.dumps({
            'command': command or [sys.executable, '-m', 'dorsopathy_workers.mri_pathology'],
            'timeout_seconds': timeout,
        }), encoding='utf-8')
        return path

    def test_unconfigured_registry_has_no_pathology_or_worker_workspace(self):
        store = self.root / 'store'
        registry = build_registry(store)
        self.assertEqual({entry['module_id'] for entry in registry.describe()}, {
            'complaints', 'clinical.exam', 'mri.anatomy', 'lidar.posture',
            'thermography', 'emg.surface', 'ultrasound', 'nociception',
        })
        with self.assertRaises(RegistryError):
            registry.get('mri.pathology', '0.1.0')
        self.assertFalse((store / 'mri-pathology-workers').exists())

    def test_pathology_is_opt_in_and_independent_of_anatomy_worker(self):
        command = ['/trusted/path/to/python', '-m', 'dorsopathy_workers.mri_pathology']
        config = self.config(command=command, timeout=17.5)
        store = self.root / 'store'
        with patch('dorsopathy_platform.process_provider.subprocess.Popen') as start:
            registry = build_registry(store, pathology_config=config)
            start.assert_not_called()
        provider = registry.get('mri.pathology', '0.1.0')
        self.addCleanup(provider.close)
        self.assertIsInstance(provider, ProcessProvider)
        self.assertEqual(provider.command, tuple(command))
        self.assertEqual(provider.timeout_seconds, 17.5)
        self.assertEqual(provider.workspace_root, (store / 'mri-pathology-workers').resolve())
        self.assertEqual(provider.spec.display_name, 'MRI pathology (SpineNet research)')
        self.assertFalse(provider.spec.synthetic_only)
        self.assertIsNone(registry.get('mri.anatomy', '0.1.0').live_provider)
        self.assertFalse((store / 'mri-workers').exists())

    def test_anatomy_and_pathology_have_separate_commands_and_workspaces(self):
        anatomy = self.config('anatomy.json', ['/trusted/anatomy/python', 'anatomy.py'], 31)
        pathology = self.config('pathology.json', ['/trusted/pathology/python', 'pathology.py'], 47)
        store = self.root / 'store'
        registry = build_registry(store, anatomy, pathology)
        first = registry.get('mri.anatomy', '0.1.0').live_provider
        second = registry.get('mri.pathology', '0.1.0')
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertEqual(first.command, ('/trusted/anatomy/python', 'anatomy.py'))
        self.assertEqual(second.command, ('/trusted/pathology/python', 'pathology.py'))
        self.assertEqual(first.workspace_root, (store / 'mri-workers').resolve())
        self.assertEqual(second.workspace_root, (store / 'mri-pathology-workers').resolve())
        self.assertEqual((first.timeout_seconds, second.timeout_seconds), (31.0, 47.0))

    def test_trusted_config_validation_is_identical_for_both_workers(self):
        invalid = [
            [], {}, {'command': ['python']},
            {'command': ['python'], 'timeout_seconds': 1, 'shell': True},
            {'command': 'python worker.py', 'timeout_seconds': 1},
            {'command': [], 'timeout_seconds': 1},
            {'command': [''], 'timeout_seconds': 1},
            {'command': [1], 'timeout_seconds': 1},
            {'command': ['python\x00worker'], 'timeout_seconds': 1},
        ]
        invalid += [{'command': ['python'], 'timeout_seconds': value}
                    for value in [True, None, '600', 0, 0.99, 3600.01,
                                  float('nan'), float('inf'), 10 ** 400]]
        path = self.root / 'invalid.json'
        for argument in ('mri_config', 'pathology_config'):
            for index, value in enumerate(invalid):
                with self.subTest(argument=argument, case=index):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        build_registry(self.root / 'store', **{argument: path})
        self.assertFalse((self.root / 'store').exists())

    def test_oversized_and_ambiguous_configs_are_rejected(self):
        path = self.root / 'invalid.json'
        documents = [
            ' ' * 65537,
            '{"command":["python"],"command":["different"],"timeout_seconds":1}',
            '{"command":["python"],"timeout_seconds":NaN}',
        ]
        for argument in ('mri_config', 'pathology_config'):
            for index, content in enumerate(documents):
                with self.subTest(argument=argument, case=index):
                    path.write_text(content, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        build_registry(self.root / 'store', **{argument: path})

    def test_valid_size_and_timeout_boundaries_are_accepted(self):
        for argument in ('mri_config', 'pathology_config'):
            for timeout in (1, 3600):
                with self.subTest(argument=argument, timeout=timeout):
                    path = self.config(timeout=timeout)
                    data = path.read_bytes()
                    path.write_bytes(data + b' ' * (65536 - len(data)))
                    registry = build_registry(self.root / 'store', **{argument: path})
                    provider = (registry.get('mri.anatomy', '0.1.0').live_provider
                                if argument == 'mri_config' else registry.get('mri.pathology', '0.1.0'))
                    try:
                        self.assertEqual(provider.timeout_seconds, float(timeout))
                    finally:
                        provider.close()

    def test_invalid_pathology_config_constructs_neither_worker(self):
        anatomy = self.config('anatomy.json')
        pathology = self.root / 'bad-pathology.json'
        pathology.write_text('{"command":"shell text","timeout_seconds":600}', encoding='utf-8')
        with patch('dorsopathy_platform.bootstrap.ProcessProvider') as provider:
            with self.assertRaises(ValueError):
                build_registry(self.root / 'store', anatomy, pathology)
            provider.assert_not_called()

    def test_existing_application_positional_arguments_keep_their_meaning(self):
        app = create_application(self.root / 'legacy', None, 2, 4096)
        try:
            self.assertEqual(app.uploads.max_bytes, 4096)
            self.assertEqual(app.dispatcher._cpu._max_workers, 2)
            self.assertNotIn('mri.pathology', {entry['module_id'] for entry in app.modules()})
        finally:
            app.close()
        config = self.config()
        app = create_application(self.root / 'configured', None, 2, 8192, pathology_config=config)
        try:
            self.assertEqual(app.uploads.max_bytes, 8192)
            self.assertEqual(app.registry.get('mri.pathology', '0.1.0').spec.module_id, 'mri.pathology')
        finally:
            app.close()

    def test_failed_config_releases_application_store_lock(self):
        config = self.root / 'invalid.json'
        config.write_text('{}', encoding='utf-8')
        store = self.root / 'store'
        with self.assertRaises(ValueError):
            create_application(store, pathology_config=config)
        app = create_application(store)
        app.close()

    def test_pathology_shares_single_mri_lane_without_blocking_cpu(self):
        app = create_application(self.root / 'store', None, 1, pathology_config=self.config())
        entered = threading.Event()
        release = threading.Event()
        pathology_entered = threading.Event()
        def anatomy(request, context):
            entered.set()
            release.wait(3)
            return result_envelope(request, [], [])
        def pathology(request, context):
            pathology_entered.set()
            return result_envelope(request, [], [])
        app.registry.get('mri.anatomy', '0.1.0').analyze = anatomy
        app.registry.get('mri.pathology', '0.1.0').analyze = pathology
        app.registry.get('complaints', '0.1.0').analyze = lambda request, context: result_envelope(request, [], [])
        try:
            episode_id = app.create_episode()['episode_id']
            for module_id, job_id in [('mri.anatomy', 'job-anatomy'), ('mri.pathology', 'job-pathology'),
                                      ('complaints', 'job-cpu')]:
                app.repository.create_job({
                    'schema_version': '1.0', 'job_id': job_id, 'episode_id': episode_id,
                    'module_id': module_id, 'module_version': '0.1.0',
                    'input_artifacts': [], 'parameters': {}, 'protocol_id': None,
                })
            first = app.dispatcher.enqueue('job-anatomy')
            self.assertTrue(entered.wait(2))
            second = app.dispatcher.enqueue('job-pathology')
            cpu = app.dispatcher.enqueue('job-cpu')
            self.assertEqual(cpu.result(2)['status'], 'complete')
            self.assertFalse(pathology_entered.is_set())
            self.assertEqual(app.repository.get_job('job-pathology')['status'], 'queued')
            release.set()
            self.assertEqual(first.result(3)['status'], 'complete')
            self.assertEqual(second.result(3)['status'], 'complete')
            self.assertTrue(pathology_entered.is_set())
        finally:
            release.set()
            app.close()

    def test_configured_bootstrap_never_imports_model_dependencies(self):
        code = textwrap.dedent('''
            import json, pathlib, sys, tempfile
            blocked = {'torch', 'torchvision', 'SimpleITK', 'numpy', 'pydicom',
                       'cv2', 'spinenet', 'nnunetv2', 'PySide6', 'dorsopathy_modules.mri_pathology'}
            class BlockModelImports:
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                        raise RuntimeError('Model dependency imported by platform: ' + fullname)
            sys.meta_path.insert(0, BlockModelImports())
            from dorsopathy_platform.bootstrap import create_application
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                config = root / 'worker.json'
                config.write_text(json.dumps({'command': [sys.executable, '-m', 'dorsopathy_workers.mri_pathology'],
                                              'timeout_seconds': 1}))
                app = create_application(root / 'store', pathology_config=config)
                try:
                    assert 'mri.pathology' in {entry['module_id'] for entry in app.modules()}
                    assert not any(name in sys.modules for name in blocked)
                finally:
                    app.close()
        ''')
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', code], cwd=repository,
                                env={**os.environ, 'PYTHONPATH': str(repository / 'src')},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_routes_pathology_config_for_serve(self):
        app = MagicMock()
        server = MagicMock()
        server.server_port = 8766
        server.serve_forever.side_effect = KeyboardInterrupt
        config = self.root / 'pathology.json'
        anatomy = self.root / 'anatomy.json'
        store = self.root / 'store'
        with patch('dorsopathy_platform.bootstrap.create_application', return_value=app) as factory, \
             patch('dorsopathy_platform.http.create_server', return_value=server), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(['serve', '--store', str(store), '--mri-config', str(anatomy),
                                   '--mri-pathology-config', str(config)]), 0)
        factory.assert_called_once_with(store, anatomy, pathology_config=config, raw_workers_config=None)
        server.server_close.assert_called_once_with()
        app.close.assert_called_once_with()

    def test_cli_routes_pathology_config_for_run_flow(self):
        app = MagicMock()
        app.get_snapshot.return_value = {'results': []}
        config = self.root / 'pathology.json'
        anatomy = self.root / 'anatomy.json'
        store = self.root / 'store'
        completed = {'status': 'complete', 'snapshot_id': 'snapshot-test',
                     'report_artifact': {'artifact_id': 'report-test'}}
        with patch('dorsopathy_platform.bootstrap.create_application', return_value=app) as factory, \
             patch('dorsopathy_platform.demo_flow.start_demo', return_value={'run_id': 'run-test'}), \
             patch('dorsopathy_platform.workflow.wait_for_run', return_value=completed), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(['run-flow', '--store', str(store), '--mri-config', str(anatomy),
                                   '--mri-pathology-config', str(config)]), 0)
        factory.assert_called_once_with(store, anatomy, pathology_config=config, raw_workers_config=None)
        app.close.assert_called_once_with()

    def test_cli_invalid_config_returns_safe_error(self):
        config = self.root / 'invalid.json'
        config.write_text('{"command":"PRIVATE_CONFIGURATION_TEXT","timeout_seconds":600}', encoding='utf-8')
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(['serve', '--store', str(self.root / 'store'), '--mri-pathology-config', str(config)])
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE_CONFIGURATION_TEXT', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())


if __name__ == '__main__':
    unittest.main()
