"""Standalone raw-signal provider; SciPy is loaded only in its isolated worker."""
from pathlib import Path
import sys
import tempfile

from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.process_provider import ProcessProvider

RAW_EMG_SPEC = ProviderSpec("emg.surface", "0.2.0", "Raw surface EMG: signal QC, filtering and window features")


class RawEMGProvider:
    spec = RAW_EMG_SPEC

    def __init__(self, *, python_executable=None, workspace_root=None, timeout_seconds=120):
        # These arguments belong to the trusted local operator, never ModuleRequest.
        executable = str(Path(python_executable or sys.executable).absolute())
        package_root = str(Path(__file__).resolve().parents[2])
        launcher = (
            executable, "-c",
            "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('dorsopathy_workers.emg',run_name='__main__')",
            package_root,
        )
        self._process = ProcessProvider(
            self.spec, launcher,
            workspace_root or Path(tempfile.gettempdir()) / "dorsopathy-emg-workers",
            timeout_seconds=timeout_seconds,
        )

    def analyze(self, request, context):
        return self._process.analyze(request, context)

    def close(self):
        self._process.close()

    shutdown = close
