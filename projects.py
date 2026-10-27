"""Bounded editable sessions and atomic revisions, independent of finished-job expiry."""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from editing import (
    apply_edit,
    integer,
    protection_boxes,
    protection_mask,
    rectangles,
    region_data,
    validate_numbers,
)
from jobs import JobCapacityError, JobNotFound
from project_io import MAX_ACTIONS, pack_document, png_bytes, unpack_document

logger = logging.getLogger(__name__)
PROJECT_BYTES = 512 * 1024 * 1024
TOTAL_BYTES = 1024 * 1024 * 1024


def disk_size(root):
    size = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                size += path.stat().st_size
        except FileNotFoundError:
            pass  # Another session may have just closed or pruned this file.
    return size


class RevisionConflict(ValueError):
    pass


def fingerprint(state):
    digest = hashlib.sha256()
    for key in ("source", "raw", "labels", "centers"):
        digest.update(state[key].tobytes())
    digest.update(
        json.dumps(
            {
                k: state[k]
                for k in (
                    "params",
                    "manual",
                    "detected",
                    "disabled",
                    "faces",
                    "overrides",
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return digest.hexdigest()


def change_document(document, command):
    kind = command.get("type")
    fields = {
        "undo": {"type"},
        "redo": {"type"},
        "recolor": {"type", "region", "color"},
        "merge": {"type", "region", "target"},
        "number": {"type", "region", "x", "y"},
        "regenerate": {"type", "manual", "disabled"},
    }
    required = fields.get(kind) if isinstance(kind, str) else None
    allowed = (
        required | ({"eye_detail"} if kind == "regenerate" else set())
        if required
        else set()
    )
    if required is None or not required <= set(command) or set(command) - allowed:
        raise ValueError("Unknown command or unexpected command fields.")
    document = dict(document, states=list(document["states"]))
    cursor = document["cursor"]
    if kind in {"undo", "redo"}:
        target = cursor + (-1 if kind == "undo" else 1)
        if not 0 <= target < len(document["states"]):
            raise ValueError("There is no action to " + kind + ".")
        document["cursor"] = target
        return document
    state = document["states"][cursor]
    if kind == "regenerate":
        from pipeline import bilateral_filter, preserve_eye_pixels, quantize_image
        from regions import cleanup_regions
        from canvas_size import painting_geometry

        manual = rectangles(command["manual"])
        disabled = command["disabled"]
        if not isinstance(disabled, list) or len(disabled) > len(state["detected"]):
            raise ValueError("Invalid disabled eye list.")
        for index in disabled:
            integer(index, "Detected eye", 0, len(state["detected"]) - 1)
        if len(set(disabled)) != len(disabled):
            raise ValueError("Duplicate detected eye.")
        detail = command.get("eye_detail", state["params"].get("eye_detail", "fine"))
        if not isinstance(detail, str) or detail not in {"fine", "balanced"}:
            raise ValueError("Eye detail must be fine or balanced.")
        state = dict(
            state,
            manual=manual,
            disabled=disabled,
            overrides=[],
            params=dict(state["params"], eye_detail=detail),
        )
        mask = protection_mask(state)
        p = state["params"]
        source = state["source"]
        smoothed = bilateral_filter(
            source, p["bilateral_diameter"], p["sigma_color"], p["sigma_color"]
        )
        smoothed = preserve_eye_pixels(source, smoothed, mask, detail=detail)
        raw, centers = quantize_image(smoothed, p["k"], color_space=p["color_space"])
        labels = cleanup_regions(
            raw,
            p["k"],
            centers=centers,
            protected_mask=mask,
            **painting_geometry(source.shape[1], source.shape[0], p),
        )
        state = dict(
            state,
            raw=raw.astype(np.uint8),
            centers=centers,
            labels=labels.astype(np.uint8),
            title="Regenerated eye protection",
        )
    else:
        previous_overrides = len(state["overrides"])
        state = apply_edit(state, command)
        state["title"] = {
            "merge": "Merged adjacent regions",
            "recolor": "Recolored region",
            "number": "Placed number",
        }[kind]
        reset = previous_overrides - len(state["overrides"])
        if kind != "number" and reset > 0:
            state["title"] += f" (reset {reset} manual number position(s))"
    document["states"] = document["states"][: cursor + 1] + [state]
    if len(document["states"]) > MAX_ACTIONS + 1:
        document["states"].pop(0)
        document["pruned"] += 1
    document["cursor"] = len(document["states"]) - 1
    # Atomic rejection if retaining this transaction would exceed the byte budget.
    while True:
        try:
            pack_document(document)
            break
        except ValueError as exc:
            if "undo budget" not in str(exc) or len(document["states"]) <= 2:
                raise
            document["states"].pop(0)
            document["cursor"] -= 1
            document["pruned"] += 1
    return document


def process_project_job(job_id, shared_state, directory, params):
    """All untrusted bundle decoding and expensive editing stay in owned workers."""
    from pipeline import render_result
    from exports import image_bytes

    root = Path(directory)
    try:
        cv2.setNumThreads(max(1, min(2, (os.cpu_count() or 2) // 2)))
        cv2.ocl.setUseOpenCL(False)
        shared_state[job_id] = {"status": "Opening project", "progress": 10}
        document = unpack_document((root / "input.png").read_bytes())
        for state in document["states"]:
            validate_numbers(state)
        if params.get("_command"):
            shared_state[job_id] = {"status": "Applying edit", "progress": 35}
            document = change_document(document, params["_command"])
        state = document["states"][document["cursor"]]
        validate_numbers(state)
        shared_state[job_id] = {"status": "Rendering revision", "progress": 65}
        result = render_result(
            state["labels"],
            state["centers"],
            state["params"],
            protection_mask(state),
            protection_boxes(state),
            state["faces"],
            overrides=state["overrides"],
        )
        regions, stats, colors = region_data(state)
        numbered = np.zeros(len(stats), np.bool_)
        for item in result["label_placements"]:
            x, y = item["anchor"]
            numbered[regions[y, x]] = True
        for name, array in [
            ("regions", regions),
            ("stats", stats),
            ("colors", colors),
            ("numbered", numbered),
        ]:
            np.save(root / (name + ".npy"), array, allow_pickle=False)
        for key, name in [
            ("processed_image", "preview"),
            ("template_image", "template"),
            ("label_map", "labels"),
        ]:
            (root / (name + ".png")).write_bytes(image_bytes(result[key]))
        (root / "missing.png").write_bytes(
            image_bytes(result["missing_regions"]["mask"])
        )
        (root / "source.png").write_bytes(png_bytes(state["source"]))
        result["editor"] = dict(
            fingerprint=fingerprint(state),
            manual=state["manual"],
            detected=state["detected"],
            disabled=state["disabled"],
            eye_detail=state["params"].get("eye_detail", "fine"),
            has_original=bool(document.get("original")),
            cursor=document["cursor"],
            history=[s["title"] for s in document["states"]],
            pruned=document.get("pruned", 0),
            region_count=len(stats),
        )
        (root / "document.pbn").write_bytes(pack_document(document, result=result))
        # Prepare both save variants inside the worker; saving never decodes an archive in the API thread.
        (root / "portable.pbn").write_bytes(
            pack_document(document, result=result, include_original=False)
        )
        pending = root / "result.tmp"
        pending.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
        pending.replace(root / "result.json")
        metadata = dict(result)
        for key in ("processed_image", "template_image", "label_map"):
            metadata.pop(key)
        metadata["missing_regions"] = {
            k: v for k, v in result["missing_regions"].items() if k != "mask"
        }
        (root / "metadata.json").write_text(
            json.dumps(metadata, allow_nan=False), encoding="utf-8"
        )
        shared_state[job_id] = {
            "status": "Complete",
            "progress": 100,
            "schema_version": 2,
        }
    except Exception as exc:
        logger.exception("Project operation failed for job %s", job_id)
        shared_state[job_id] = {
            "status": "Error",
            "progress": 0,
            "error_code": "project_failed",
            "error": str(exc)
            if isinstance(exc, ValueError)
            else "Project operation failed. The previous design is unchanged.",
        }
    finally:
        (root / "input.png").unlink(missing_ok=True)


@dataclass
class Project:
    directory: Path
    revision: int = 0
    pending: str | None = None
    error: str | None = None
    requests: dict = field(default_factory=dict)
    readers: dict = field(default_factory=dict)
    lock: object = field(default_factory=threading.RLock)
    closed: bool = False
    creation_digest: str = ""


class ProjectManager:
    def __init__(self, jobs, *, root=None, max_projects=4):
        self.jobs = jobs
        self.root = Path(tempfile.mkdtemp(prefix="paint-projects-", dir=root)).resolve()
        self.projects = {}
        self.lock = threading.RLock()
        self.max_projects = max_projects
        self.closed = False
        self.cancelled = {}
        self.reserved_bytes = 0

    def _project(self, project_id):
        with self.lock:
            if (
                self.closed
                or project_id not in self.projects
                or self.projects[project_id].closed
            ):
                raise JobNotFound(project_id)
            return self.projects[project_id]

    def sessions(self):
        with self.lock:
            return [
                {
                    "project_id": key,
                    "revision": value.revision,
                    "pending": bool(value.pending),
                }
                for key, value in self.projects.items()
                if not value.closed
            ]

    def create(self, data, request_id):
        project_id = str(uuid.UUID(request_id))
        with self.lock:
            self.cancelled = {
                key: stamp
                for key, stamp in self.cancelled.items()
                if time.monotonic() - stamp < 900
            }
            if self.closed:
                raise JobCapacityError("The server is shutting down.")
            if project_id in self.cancelled:
                raise RevisionConflict(
                    "This project opening was cancelled. Open it again with a new request ID."
                )
            if project_id in self.projects:
                if (
                    self.projects[project_id].creation_digest
                    != hashlib.sha256(data).hexdigest()
                ):
                    raise RevisionConflict(
                        "A request ID cannot be reused for another project file."
                    )
                return {"project_id": project_id}
            if len(self.projects) >= self.max_projects:
                raise JobCapacityError(
                    "Four projects are already open. Close an unused project first."
                )
            directory = self.root / project_id
            directory.mkdir()
            project = Project(
                directory, creation_digest=hashlib.sha256(data).hexdigest()
            )
            self.projects[project_id] = project
            try:
                project.pending = self.jobs.start(
                    data, {"_project_task": True}, str(uuid.uuid4())
                )
            except Exception:
                self.projects.pop(project_id)
                directory.rmdir()
                raise
        return {"project_id": project_id}

    def from_job(self, job_id, request_id):
        with self.jobs.result_lease(job_id) as (job, state):
            if state.get("status") != "Complete":
                raise RevisionConflict("Wait for processing to finish before editing.")
            data = (job.directory / "document.pbn").read_bytes()
        return self.create(data, request_id)

    def _refresh(self, project):
        if project.closed:
            raise JobNotFound("Project closed")
        if not project.pending:
            return
        try:
            with self.jobs.result_lease(project.pending) as (job, state):
                status = state.get("status")
                if status not in {"Complete", "Error", "Stopped"}:
                    return
                if status != "Complete":
                    project.error = (
                        state.get("error")
                        or "Operation cancelled. The previous design is unchanged."
                    )
                    project.pending = None
                    return
                size = sum(
                    f.stat().st_size for f in job.directory.iterdir() if f.is_file()
                )
                with self.lock:
                    total = disk_size(self.root)
                    owned = disk_size(project.directory)
                    if (
                        owned + size > PROJECT_BYTES
                        or total + self.reserved_bytes + size > TOTAL_BYTES
                    ):
                        project.error = "Project storage quota reached. Save and close an unused project."
                        project.pending = None
                        return
                    self.reserved_bytes += size
                revision = project.revision + 1
                staging = project.directory / ("pending-" + str(uuid.uuid4()))
                try:
                    staging.mkdir()
                    for name in (
                        "result.json",
                        "metadata.json",
                        "document.pbn",
                        "portable.pbn",
                        "preview.png",
                        "template.png",
                        "labels.png",
                        "missing.png",
                        "source.png",
                        "regions.npy",
                        "stats.npy",
                        "colors.npy",
                        "numbered.npy",
                    ):
                        shutil.copyfile(job.directory / name, staging / name)
                    staging.rename(project.directory / str(revision))
                except Exception:
                    if staging.exists():
                        try:
                            shutil.rmtree(staging)
                        except OSError:
                            logger.warning(
                                "Staging cleanup deferred until project close",
                                exc_info=True,
                            )
                    raise
                finally:
                    with self.lock:
                        self.reserved_bytes -= size
                project.revision = revision
                project.pending = None
                project.error = None
        except JobNotFound:
            project.pending = None
            project.error = (
                "The pending operation expired. The previous design is unchanged."
            )
        except OSError:
            logger.exception("Could not commit project revision")
            project.pending = None
            project.error = "The new revision could not be stored. Check free disk space; your previous design is unchanged."
        # Keep the current and previous immutable revision; readers pin older files.
        for path in project.directory.iterdir():
            if (
                path.name.isdecimal()
                and int(path.name) < project.revision - 1
                and not project.readers.get(int(path.name))
            ):
                try:
                    shutil.rmtree(path)
                except OSError:
                    logger.warning(
                        "Old project revision cleanup will be retried", exc_info=True
                    )

    def snapshot(self, project_id):
        project = self._project(project_id)
        with project.lock:
            self._refresh(project)
            revision, pending, error = project.revision, project.pending, project.error
            result = None
            if revision:
                result = json.loads(
                    (project.directory / str(revision) / "metadata.json").read_text(
                        encoding="utf-8"
                    )
                )
                base = f"/api/projects/{project_id}/revisions/{revision}"
                for key, name in [
                    ("processed_image", "preview"),
                    ("template_image", "template"),
                    ("label_map", "labels"),
                ]:
                    result[key] = base + "/assets/" + name + ".png"
                result["missing_regions"]["mask"] = base + "/assets/missing.png"
                result["editor"]["source"] = base + "/assets/source.png"
            operation = dict(self.jobs.states.get(pending, {})) if pending else None
            return dict(
                project_id=project_id,
                revision=revision,
                pending=pending,
                error=error,
                result=result,
                operation=operation,
            )

    def command(self, project_id, expected, request_id, command):
        project = self._project(project_id)
        signature = json.dumps(
            {"expected": expected, "command": command}, sort_keys=True
        )
        with project.lock:
            self._refresh(project)
            if request_id in project.requests:
                prior, response = project.requests[request_id]
                if prior != signature:
                    raise RevisionConflict(
                        "A request ID cannot be reused for a different edit."
                    )
                return response
            if project.pending or project.revision != expected or not expected:
                raise RevisionConflict(
                    "The project changed or an operation is running. Refresh before editing."
                )
            data = (project.directory / str(expected) / "document.pbn").read_bytes()
            job_id = self.jobs.start(
                data, {"_project_task": True, "_command": command}, str(uuid.uuid4())
            )
            project.pending, project.error = job_id, None
            response = {
                "job_id": job_id,
                "project_id": project_id,
                "base_revision": expected,
            }
            project.requests[request_id] = signature, response
            if len(project.requests) > 128:
                del project.requests[next(iter(project.requests))]
            return response

    def cancel(self, project_id):
        project = self._project(project_id)
        with project.lock:
            if project.closed:
                raise JobNotFound("Project closed")
            if project.pending:
                try:
                    self.jobs.stop(project.pending)
                except JobNotFound:
                    pass
                # Even a finished worker is still a candidate until project commit.
                project.pending = None
                project.error = "Operation cancelled. The previous design is unchanged."
            return {"status": "Stopped"}

    @contextmanager
    def lease(self, project_id, revision):
        project = self._project(project_id)
        with project.lock:
            if project.closed:
                raise JobNotFound("Project closed")
            path = project.directory / str(revision)
            if not path.is_dir():
                raise JobNotFound("Revision expired")
            project.readers[revision] = project.readers.get(revision, 0) + 1
        try:
            yield path
        finally:
            with project.lock:
                project.readers[revision] -= 1

    def cache_export(self, project_id, path, data):
        """Export caching is optional; reserve bytes without holding locks during I/O."""
        project = self._project(project_id)
        if path.parent.parent != project.directory:
            raise ValueError("Invalid export ownership path.")
        with self.lock:
            total = disk_size(self.root)
            owned = disk_size(project.directory)
            if (
                owned + len(data) > PROJECT_BYTES
                or total + self.reserved_bytes + len(data) > TOTAL_BYTES
            ):
                return
            self.reserved_bytes += len(data)
        pending = path.parent / (str(uuid.uuid4()) + ".tmp")
        try:
            pending.write_bytes(data)
            pending.replace(path)
        except OSError:
            logger.warning(
                "Export cache unavailable; serving generated bytes", exc_info=True
            )
        finally:
            try:
                pending.unlink(missing_ok=True)
            finally:
                with self.lock:
                    self.reserved_bytes -= len(data)

    def close_project(self, project_id):
        with self.lock:
            project = self.projects.get(project_id)
            if project is None:
                self.cancelled = {
                    key: stamp
                    for key, stamp in self.cancelled.items()
                    if time.monotonic() - stamp < 900
                }
                if len(self.cancelled) >= 128 and project_id not in self.cancelled:
                    raise JobCapacityError(
                        "Too many pending project cancellations. Try again shortly."
                    )
                self.cancelled[project_id] = time.monotonic()
                return
        with project.lock:
            if any(project.readers.values()):
                raise RevisionConflict(
                    "A download is still in progress. Try closing again."
                )
            if project.pending:
                try:
                    self.jobs.stop(project.pending)
                except JobNotFound:
                    pass
            # Only this UUID directory inside the manager's resolved root is owned.
            if project.directory.resolve().parent != self.root:
                raise ValueError("Invalid project ownership path.")
            shutil.rmtree(project.directory)
            project.closed = True
            with self.lock:
                self.projects.pop(project_id, None)

    def close(self):
        errors = []
        for project_id in list(self.projects):
            try:
                self.close_project(project_id)
            except Exception as exc:
                errors.append(exc)
        self.closed = True
        if not self.projects:
            self.root.rmdir()
        if errors:
            raise ExceptionGroup("Project cleanup failed", errors)
