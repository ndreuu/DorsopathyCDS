"""Blocking CLI convenience on the same persisted asynchronous workflow."""
import time
import math

def wait_for_run(application, run_id, timeout_seconds=600, poll_seconds=0.05):
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
        raise ValueError('Workflow timeout must be positive and finite')
    if type(poll_seconds) not in (int,float) or not math.isfinite(poll_seconds) or poll_seconds<=0:
        raise ValueError('Workflow polling interval must be positive and finite')
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = application.get_run(run_id)
        if run['status'] != 'running':
            return run
        if time.monotonic() >= deadline:
            raise TimeoutError('Workflow is still processing; inspect its persisted run')
        time.sleep(poll_seconds)
