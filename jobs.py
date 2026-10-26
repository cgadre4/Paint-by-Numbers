"""Own worker processes and temporary data for the lifetime of one server."""
import json
import hashlib
import os
import logging
import multiprocessing as mp
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from contextlib import contextmanager
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
    readers: int = 0
    cluster_key: str = ""
    export_lock: object = field(default_factory=threading.Lock)


class JobManager:
    def __init__(self, *, root=None, max_active=2, max_retained=8, ttl=900, timeout=300,
                 worker=process_job_worker):
        self.context = mp.get_context("spawn")
        self.manager = self.context.Manager()
        try:
            self.states = self.manager.dict()
            self.root = Path(tempfile.mkdtemp(prefix="paint-by-numbers-", dir=root)).resolve()
        except Exception:
            self.manager.shutdown()
            raise
        self.jobs = {}
        self.lock = threading.RLock()
        self.idle_readers = threading.Condition(self.lock)
        self.closing = False
        self.max_active, self.max_retained = max_active, max_retained
        self.ttl, self.timeout, self.worker = ttl, timeout, worker
        self.closed = False
        self.finished_sequence = 0
        self.cancelled = {}

    def start(self, image, params, request_id=None):
        upstream = {name: params.get(name) for name in ("k", "resolution_factor", "bilateral_diameter", "sigma_color", "color_space", "protect_eyes")}
        cluster_key = hashlib.sha256(image + json.dumps(upstream, sort_keys=True).encode()).hexdigest()
        with self.lock:
            self.reap()
            if self.closed or self.closing:
                raise JobCapacityError("The server is shutting down.")
            if request_id in self.cancelled:
                raise JobCapacityError("This request was cancelled. Start a new request.")
            if request_id in self.jobs:
                return request_id
            if sum(job.process is not None for job in self.jobs.values()) >= self.max_active:
                raise JobCapacityError("All processing slots are busy. Stop a job or try again shortly.")
            job_id = request_id or str(uuid.uuid4())
            # Validate even internal callers before using an identifier as a path.
            job_id = str(uuid.UUID(job_id))
            directory = self.root / job_id
            directory.mkdir()
            process = self.context.Process(target=self.worker,
                args=(job_id, self.states, str(directory), params), daemon=True)
            try:
                (directory / "input.png").write_bytes(image)
                for previous in self.jobs.values():
                    cached = previous.directory / "clusters.npz"
                    if previous.cluster_key == cluster_key and cached.exists():
                        try:
                            # Immutable, same-filesystem hard link owns its lifetime independently.
                            os.link(cached, directory / "clusters.npz")
                        except OSError:
                            pass  # Cache reuse is optional on filesystems without hard links.
                        break
                self.states[job_id] = {"status": "Queued", "progress": 0}
                process.start()
                self.jobs[job_id] = Job(directory, process, time.monotonic(), cluster_key=cluster_key)
            except Exception:
                if process.pid is not None:
                    self._join(process, terminate=True)
                else:
                    process.close()
                self.states.pop(job_id, None)
                try:
                    self._remove_directory(directory)
                except OSError:
                    logger.exception("Failed start directory cleanup deferred to shutdown")
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
        try:
            (job.directory / "input.png").unlink(missing_ok=True)
        except OSError:
            logger.warning("Input removal deferred for job %s", job_id)
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
            self.cancelled = {key: stamp for key, stamp in self.cancelled.items() if now - stamp < self.ttl}
            for job_id, job in list(self.jobs.items()):
                try:
                    if job.process is not None:
                        state = self.states.get(job_id, {})
                        if state.get("status") in TERMINAL:
                            # The worker has atomically published its result. Join its
                            # short exit cleanup now so completed jobs release capacity.
                            self._finish(job_id, job)
                        elif not job.process.is_alive():
                            self._finish(job_id, job, "Error", "Processing worker exited unexpectedly.")
                        elif now - job.started >= self.timeout:
                            self._finish(job_id, job, "Error", "Processing exceeded the five-minute time limit. Try a lower resolution.")
                except Exception:
                    logger.exception("Could not reap job %s; will retry", job_id)
            # Clock ticks can tie on Windows; retain the actual completion order.
            completed = sorted((job.finished, job.finished_order, job_id)
                               for job_id, job in self.jobs.items() if job.finished is not None)
            for position, (finished, _, job_id) in enumerate(completed):
                if now - finished >= self.ttl or position < len(completed) - self.max_retained:
                    job = self.jobs[job_id]
                    if job.readers:
                        continue
                    try:
                        self._remove_directory(job.directory)
                    except OSError:
                        logger.exception("Could not expire job %s; will retry", job_id)
                        continue
                    del self.jobs[job_id]
                    self.states.pop(job_id, None)

    @contextmanager
    def result_lease(self, job_id):
        with self.lock:
            self.reap()
            if self.closing or job_id not in self.jobs:
                raise JobNotFound(job_id)
            job = self.jobs[job_id]
            job.readers += 1
            state = dict(self.states[job_id])
        try:
            yield job, state
        finally:
            with self.lock:
                job.readers -= 1
                self.idle_readers.notify_all()

    def snapshot(self, job_id):
        # Parse large immutable results outside the process-management lock.
        with self.result_lease(job_id) as (job, state):
            if state.get("status") == "Complete":
                try:
                    state["result"] = json.loads((job.directory / "result.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {"status": "Error", "progress": 0, "error": "The result could not be read. Process the image again."}
            return state

    def export(self, job_id, kind, format_):
        from exports import make_export
        if kind not in {"template", "palette", "tiles"} or format_ not in {"png", "pdf"} or (kind == "tiles" and format_ != "pdf"):
            raise ValueError("Unknown export format")
        with self.result_lease(job_id) as (job, state):
            with job.export_lock:
                if state.get("status") != "Complete":
                    raise ValueError("This job has no completed result to export.")
                path = job.directory / f"{kind}.{format_}"
                if path.exists():
                    return path.read_bytes()
                result = json.loads((job.directory / "result.json").read_text(encoding="utf-8"))
                data = make_export(result, kind, format_)
                pending = job.directory / (str(uuid.uuid4()) + ".tmp")
                try:
                    pending.write_bytes(data)
                    pending.replace(path)
                finally:
                    pending.unlink(missing_ok=True)
                return data

    def cancel_request(self, request_id):
        with self.lock:
            if request_id in self.jobs:
                return self.stop(request_id)
            # Bound tombstones even if a client never sends its upload.
            self.reap()
            if request_id in self.cancelled:
                return {"status": "Stopped"}
            if len(self.cancelled) >= 128:
                raise JobCapacityError("Too many pending cancellations. Try again shortly.")
            self.cancelled[request_id] = time.monotonic()
            return {"status": "Stopped"}

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
            self.closing = True
            deadline = time.monotonic() + 30
            while any(job.readers for job in self.jobs.values()) and time.monotonic() < deadline:
                self.idle_readers.wait(timeout=.1)
            protected = {job.directory for job in self.jobs.values() if job.readers}
            errors = [RuntimeError("Result readers did not finish before shutdown.")] if protected else []
            for job_id, job in list(self.jobs.items()):
                try:
                    if job.process is not None:
                        self._finish(job_id, job, "Stopped", "Server shutdown.")
                    if not job.readers:
                        self._remove_directory(job.directory)
                except Exception as exc:
                    errors.append(exc)
                    logger.exception("Shutdown cleanup failed for job %s", job_id)
            # Always stop the manager, even if one directory is temporarily locked.
            try:
                self.states.clear()
            finally:
                self.manager.shutdown()
                self.closed = True
            self.jobs.clear()
            try:
                # Includes directories left by an unsuccessful spawn; root is owned.
                for directory in self.root.iterdir():
                    if directory.is_dir() and directory not in protected:
                        self._remove_directory(directory)
                self.root.rmdir()
            except OSError as exc:
                errors.append(exc)
            if errors:
                raise ExceptionGroup("Some job files could not be cleaned up", errors)
