"""Explicit assembly; MRI execution remains in a trusted external process."""
from pathlib import Path
import math

from .application import Application
from .contracts import loads_json
from .dispatcher import Dispatcher
from .process_provider import ProcessProvider
from .registry import ModuleRegistry
from .storage import ArtifactStore,SqliteRepository
from .runtime import RuntimeLock

def build_registry(store,mri_config=None):
    from dorsopathy_modules.complaints import ComplaintsProvider
    from dorsopathy_modules.clinical_exam import ClinicalExamProvider
    from dorsopathy_modules.mri_anatomy import MRIAnatomyProvider
    from dorsopathy_modules.lidar_posture import LidarPostureProvider
    from dorsopathy_modules.thermography import ThermographyProvider
    from dorsopathy_modules.ultrasound import UltrasoundProvider
    from dorsopathy_modules.emg_surface import EMGSurfaceProvider
    from dorsopathy_modules.nociception import NociceptionProvider

    mri=MRIAnatomyProvider()
    if mri_config is not None:
        path=Path(mri_config)
        if path.stat().st_size>65536: raise ValueError('MRI configuration exceeds limit')
        config=loads_json(path.read_bytes())
        if type(config) is not dict or set(config)!={'command','timeout_seconds'}:
            raise ValueError('Invalid trusted MRI configuration')
        command=config['command']; timeout=config['timeout_seconds']
        if type(command) is not list or not command or any(type(a) is not str or not a or '\x00' in a for a in command):
            raise ValueError('MRI worker command must be a trusted argv array')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1<=timeout<=3600:
            raise ValueError('Invalid worker timeout')
        process=ProcessProvider(mri.spec,tuple(command),Path(store)/'mri-workers',timeout_seconds=timeout)
        mri=MRIAnatomyProvider(live_provider=process)
    registry=ModuleRegistry()
    for provider in [ComplaintsProvider(),ClinicalExamProvider(),mri,LidarPostureProvider(),
                     ThermographyProvider(),EMGSurfaceProvider(),UltrasoundProvider(),NociceptionProvider()]:
        registry.register(provider)
    return registry

def create_application(store,mri_config=None,max_workers=4,max_upload_bytes=512*1024*1024):
    guard=RuntimeLock(store)
    dispatcher=None
    try:
        from dorsopathy_cds.episode_report import evaluate_snapshot
        from .comparison import compare_snapshot
        repo=SqliteRepository(Path(store));artifacts=ArtifactStore(Path(store))
        registry=build_registry(store,mri_config)
        dispatcher=Dispatcher(repo,artifacts,registry,max_workers=max_workers)
        def report_builder(snapshot,manifest,reader):
            comparison=compare_snapshot(snapshot,manifest,reader)
            return evaluate_snapshot(snapshot,manifest,reader,comparison=comparison)
        app=Application(repo,artifacts,registry,dispatcher,report_builder,max_upload_bytes)
        app._runtime_lock=guard
        return app
    except BaseException:
        if dispatcher is not None:dispatcher.close()
        guard.close();raise
