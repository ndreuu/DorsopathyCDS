"""Blocking CLI convenience on the same persisted asynchronous workflow."""
import time

def wait_for_run(application, run_id, timeout_seconds=600, poll_seconds=0.05):
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = application.get_run(run_id)
        if run['status'] != 'running':
            return run
        if time.monotonic() >= deadline:
            raise TimeoutError('Workflow is still processing; inspect its persisted run')
        time.sleep(poll_seconds)
