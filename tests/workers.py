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
