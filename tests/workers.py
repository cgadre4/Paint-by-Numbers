"""Spawn-safe fixtures used to exercise real worker lifecycle failures."""
import os
import time


def waiting_worker(job_id, states, directory, params):
    states[job_id] = {"status": "Working", "progress": 20}
    time.sleep(60)


def crashing_worker(job_id, states, directory, params):
    os._exit(7)


def error_worker(job_id, states, directory, params):
    states[job_id] = {"status": "Error", "progress": 0, "error": "Intentional fixture error"}


def delayed_complete_worker(job_id, shared_state, directory, params):
    from pathlib import Path
    import time
    (Path(directory) / 'result.json').write_text('{}', encoding='utf-8')
    shared_state[job_id] = {'status': 'Complete', 'progress': 100}
    time.sleep(.5)
