"""Raw ToF/depth processing with no mandatory third-party dependencies."""
from pathlib import Path
import sys
import tempfile

from dorsopathy_platform.ports import ProviderSpec
from dorsopathy_platform.process_provider import ProcessProvider

RAW_LIDAR_SPEC = ProviderSpec("lidar.posture", "0.2.0", "Raw depth: automatic visible-surface geometry")


class RawLidarProvider:
    spec = RAW_LIDAR_SPEC

    def __init__(self, *, python_executable=None, workspace_root=None, timeout_seconds=120):
        self._process = None
        if python_executable is not None:
            executable = str(Path(python_executable).absolute())
            package_root = str(Path(__file__).resolve().parents[2])
            command = (
                executable, "-c",
                "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                "runpy.run_module('dorsopathy_workers.lidar',run_name='__main__')",
                package_root,
            )
            self._process = ProcessProvider(
                self.spec, command, workspace_root or Path(tempfile.gettempdir()) / "dorsopathy-lidar-workers",
                timeout_seconds=timeout_seconds,
            )

    def analyze(self, request, context):
        if self._process is not None:
            return self._process.analyze(request, context)
        from .analysis import analyze_depth
        return analyze_depth(request, context)

    def close(self):
        if self._process is not None:
            self._process.close()

    shutdown = close
