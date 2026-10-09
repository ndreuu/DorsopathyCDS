"""One controller per local store: recovery must never interrupt another server."""
import fcntl
import os
from pathlib import Path

class RuntimeLock:
    def __init__(self,root):
        self.descriptor=None
        root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
        descriptor=os.open(root/'.runtime.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise ValueError('Store already has an active controller; use its HTTP API or another store') from None
        self.descriptor=descriptor
    def close(self):
        if self.descriptor is not None:
            fcntl.flock(self.descriptor,fcntl.LOCK_UN)
            os.close(self.descriptor);self.descriptor=None
