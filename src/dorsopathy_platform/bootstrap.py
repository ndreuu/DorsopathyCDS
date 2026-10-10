"""Explicit assembly; MRI execution remains in a trusted external process."""
from pathlib import Path
import math

from .application import Application
from .contracts import loads_json
from .dispatcher import Dispatcher
from .ports import ProviderSpec
from .process_provider import ProcessProvider
from .registry import ModuleRegistry
from .storage import ArtifactStore,SqliteRepository
from .runtime import RuntimeLock


def _trusted_worker_config(config_path, label):
    # Bound the actual read, including a file that grows after it is selected.
    with Path(config_path).open('rb') as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError(label + ' configuration exceeds limit')
    config = loads_json(content)
    if type(config) is not dict or set(config) != {'command', 'timeout_seconds'}:
        raise ValueError('Invalid trusted ' + label + ' configuration')
    command = config['command']
    timeout = config['timeout_seconds']
    if type(command) is not list or not command or any(
        type(arg) is not str or not arg or '\x00' in arg for arg in command
    ):
        raise ValueError(label + ' worker command must be a trusted argv array')
    if type(timeout) not in (int, float) or not 1 <= timeout <= 3600 or not math.isfinite(timeout):
        raise ValueError('Invalid worker timeout')
    return tuple(command), timeout


def build_registry(store,mri_config=None,pathology_config=None):
    # Both files are trusted operator configuration, never uploaded job input.
    mri_settings = _trusted_worker_config(mri_config, 'MRI') if mri_config is not None else None
    pathology_settings = (
        _trusted_worker_config(pathology_config, 'MRI pathology')
        if pathology_config is not None else None
    )
    from dorsopathy_modules.complaints import ComplaintsProvider
    from dorsopathy_modules.clinical_exam import ClinicalExamProvider
    from dorsopathy_modules.mri_anatomy import MRIAnatomyProvider
    from dorsopathy_modules.lidar_posture import LidarPostureProvider
    from dorsopathy_modules.thermography import ThermographyProvider
    from dorsopathy_modules.ultrasound import UltrasoundProvider
    from dorsopathy_modules.emg_surface import EMGSurfaceProvider
    from dorsopathy_modules.nociception import NociceptionProvider

    mri=MRIAnatomyProvider()
    if mri_settings is not None:
        command, timeout = mri_settings
        process=ProcessProvider(mri.spec,command,Path(store)/'mri-workers',timeout_seconds=timeout)
        mri=MRIAnatomyProvider(live_provider=process)
    providers = [ComplaintsProvider(),ClinicalExamProvider(),mri,LidarPostureProvider(),
                 ThermographyProvider(),EMGSurfaceProvider(),UltrasoundProvider(),NociceptionProvider()]
    if pathology_settings is not None:
        command, timeout = pathology_settings
        providers.append(ProcessProvider(
            ProviderSpec('mri.pathology', '0.1.0', 'MRI pathology (SpineNet research)'),
            command, Path(store)/'mri-pathology-workers', timeout_seconds=timeout,
        ))
    registry=ModuleRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


def create_application(store,mri_config=None,max_workers=4,max_upload_bytes=512*1024*1024,
                       pathology_config=None):
    guard=RuntimeLock(store)
    dispatcher=None
    try:
        from dorsopathy_cds.episode_report import evaluate_snapshot
        from .comparison import compare_snapshot
        repo=SqliteRepository(Path(store));artifacts=ArtifactStore(Path(store))
        registry=build_registry(store,mri_config,pathology_config=pathology_config)
        dispatcher=Dispatcher(repo,artifacts,registry,max_workers=max_workers,
                              mri_module_ids=('mri.anatomy', 'mri.pathology'))
        def report_builder(snapshot,manifest,reader):
            comparison=compare_snapshot(snapshot,manifest,reader)
            return evaluate_snapshot(snapshot,manifest,reader,comparison=comparison)
        app=Application(repo,artifacts,registry,dispatcher,report_builder,max_upload_bytes)
        app._runtime_lock=guard
        return app
    except BaseException:
        if dispatcher is not None:dispatcher.close()
        guard.close();raise
