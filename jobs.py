"""Own worker processes and temporary data for the lifetime of one server."""
import json
import logging
import multiprocessing as mp
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pipeline import process_job_worker

logger = logging.getLogger(__name__)
TERMINAL = {"Complete", "Error", "Stopped"}


class JobNotFound(KeyError):
    pass


class JobCapacityError(RuntimeError):
    pass


@dataclass
class Job:
    directory: Path
    process: object
    started: float
    finished: float | None = None
    finished_order: int = 0


class JobManager:
    def __init__(self, *, root=None, max_active=2, max_retained=8, ttl=900, timeout=300,
                 worker=process_job_worker):
        self.context = mp.get_context("spawn")
        self.manager = self.context.Manager()
        self.states = self.manager.dict()
        self.root = Path(tempfile.mkdtemp(prefix="paint-by-numbers-", dir=root)).resolve()
        self.jobs = {}
        self.lock = threading.RLock()
        self.max_active, self.max_retained = max_active, max_retained
        self.ttl, self.timeout, self.worker = ttl, timeout, worker
        self.closed = False
        self.finished_sequence = 0

    def start(self, image, params):
        with self.lock:
            self.reap()
            if self.closed:
                raise JobCapacityError("The server is shutting down.")
            if sum(job.process is not None for job in self.jobs.values()) >= self.max_active:
                raise JobCapacityError("All processing slots are busy. Stop a job or try again shortly.")
            job_id = str(uuid.uuid4())
            directory = self.root / job_id
            directory.mkdir()
            process = self.context.Process(target=self.worker,
                args=(job_id, self.states, str(directory), params), daemon=True)
            try:
                (directory / "input.png").write_bytes(image)
                self.states[job_id] = {"status": "Queued", "progress": 0}
                process.start()
                self.jobs[job_id] = Job(directory, process, time.monotonic())
            except Exception:
                if process.pid is not None:
                    self._join(process, terminate=True)
                else:
                    process.close()
                self.states.pop(job_id, None)
                self._remove_directory(directory)
                raise
            return job_id

    @staticmethod
    def _join(process, *, terminate=False):
        if terminate and process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        if process.is_alive():
            raise RuntimeError("The worker could not be stopped.")
        process.close()

    def _remove_directory(self, directory):
        # Every deletion is constrained to this manager's dedicated temp root.
        directory = directory.resolve()
        if directory == self.root or self.root not in directory.parents:
            raise ValueError("Invalid job cleanup path")
        shutil.rmtree(directory, ignore_errors=False)

    def _finish(self, job_id, job, status=None, error=None):
        if job.process is not None:
            self._join(job.process, terminate=status is not None)
            job.process = None
        (job.directory / "input.png").unlink(missing_ok=True)
        if status:
            self.states[job_id] = {"status": status, "progress": 0, "error": error}
        self.finished_sequence += 1
        job.finished_order = self.finished_sequence
        job.finished = time.monotonic()
        if self.states.get(job_id, {}).get("status") != "Complete":
            for name in ("result.json", "result.tmp"):
                (job.directory / name).unlink(missing_ok=True)

    def reap(self):
        with self.lock:
            if self.closed:
                return
            now = time.monotonic()
            for job_id, job in list(self.jobs.items()):
                if job.process is not None:
                    if not job.process.is_alive():
                        state = self.states.get(job_id, {})
                        if state.get("status") not in TERMINAL:
                            self._finish(job_id, job, "Error", "Processing worker exited unexpectedly.")
                        else:
                            self._finish(job_id, job)
                    elif now - job.started >= self.timeout:
                        self._finish(job_id, job, "Error", "Processing exceeded the five-minute time limit. Try a lower resolution.")
            # Clock ticks can tie on Windows; retain the actual completion order.
            completed = sorted((job.finished, job.finished_order, job_id)
                               for job_id, job in self.jobs.items() if job.finished is not None)
            for position, (finished, _, job_id) in enumerate(completed):
                if now - finished >= self.ttl or position < len(completed) - self.max_retained:
                    self._remove_directory(self.jobs[job_id].directory)
                    del self.jobs[job_id]
                    self.states.pop(job_id, None)

    def snapshot(self, job_id):
        with self.lock:
            self.reap()
            if job_id not in self.jobs:
                raise JobNotFound(job_id)
            state = dict(self.states[job_id])
            if state.get("status") == "Complete":
                try:
                    state["result"] = json.loads((self.jobs[job_id].directory / "result.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {"status": "Error", "progress": 0, "error": "The result could not be read. Process the image again."}
            return state

    def stop(self, job_id):
        with self.lock:
            self.reap()
            if job_id not in self.jobs:
                raise JobNotFound(job_id)
            job = self.jobs[job_id]
            # Completed results remain available; repeated stop requests are harmless.
            if job.process is not None:
                if self.states.get(job_id, {}).get("status") in TERMINAL:
                    self._finish(job_id, job)
                else:
                    self._finish(job_id, job, "Stopped", "Processing stopped by user.")
            return {"status": self.states[job_id]["status"]}

    def close(self):
        with self.lock:
            if self.closed:
                return
            for job_id, job in list(self.jobs.items()):
                if job.process is not None:
                    self._finish(job_id, job, "Stopped", "Server shutdown.")
                self._remove_directory(job.directory)
            self.jobs.clear()
            self.states.clear()
            self.manager.shutdown()
            self.root.rmdir()
            self.closed = True
