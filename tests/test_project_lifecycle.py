"""Project transactions preserve ownership through faults and worker cancellation."""

import uuid
from contextlib import contextmanager

import pytest

import project_io
import projects
from jobs import JobManager, JobCapacityError, JobNotFound
from project_io import pack_document
from projects import ProjectManager, RevisionConflict, change_document, fingerprint
from tests.conftest import wait_for
from tests.test_editing import document


@contextmanager
def managers(tmp_path):
    jobs = JobManager(root=tmp_path)
    manager = ProjectManager(jobs, root=tmp_path)
    try:
        yield jobs, manager
    finally:
        try:
            manager.close()
        finally:
            jobs.close()


def ready(manager, project_id):
    return wait_for(
        lambda: manager.snapshot(project_id), lambda s: not s["pending"], timeout=30
    )


def opened(manager):
    project_id = str(uuid.uuid4())
    manager.create(pack_document(document()), project_id)
    state = ready(manager, project_id)
    assert state["revision"] == 1, state
    return project_id, state


def test_creation_identity_and_cancel_before_open(tmp_path):
    with managers(tmp_path) as (jobs, manager):
        data = pack_document(document())
        cancelled = str(uuid.uuid4())
        manager.close_project(cancelled)
        with pytest.raises(RevisionConflict, match="cancelled"):
            manager.create(data, cancelled)
        project_id = str(uuid.uuid4())
        first = manager.create(data, project_id)
        assert manager.create(data, project_id) == first
        with pytest.raises(RevisionConflict, match="another project"):
            manager.create(b"changed", project_id)


def test_stale_revisions_request_reuse_and_reader_lease(tmp_path):
    with managers(tmp_path) as (jobs, manager):
        project_id, _ = opened(manager)
        request_id = str(uuid.uuid4())
        command = {"type": "merge", "region": 0, "target": 1}
        accepted = manager.command(project_id, 1, request_id, command)
        assert manager.command(project_id, 1, request_id, command) == accepted
        with pytest.raises(RevisionConflict, match="different edit"):
            manager.command(project_id, 1, request_id, {"type": "undo"})
        ready(manager, project_id)
        with pytest.raises(RevisionConflict):
            manager.command(project_id, 1, str(uuid.uuid4()), command)
        with manager.lease(project_id, 1) as path:
            original = (path / "template.png").read_bytes()
            with pytest.raises(RevisionConflict, match="download"):
                manager.close_project(project_id)
            for revision, kind in [(2, "undo"), (3, "redo")]:
                manager.command(project_id, revision, str(uuid.uuid4()), {"type": kind})
                ready(manager, project_id)
            assert (path / "template.png").read_bytes() == original
        manager.command(project_id, 4, str(uuid.uuid4()), {"type": "undo"})
        ready(manager, project_id)
        with pytest.raises(JobNotFound):
            with manager.lease(project_id, 1):
                pass


def test_cancel_timeout_and_expired_operation_preserve_committed_design(tmp_path):
    with managers(tmp_path) as (jobs, manager):
        project_id, first = opened(manager)
        manager.command(
            project_id,
            1,
            str(uuid.uuid4()),
            {"type": "regenerate", "manual": [[0.1, 0.1, 0.1, 0.1]], "disabled": []},
        )
        project = manager._project(project_id)
        jobs.stop(project.pending)
        stopped = ready(manager, project_id)
        assert stopped["revision"] == 1 and stopped["error"]
        assert (
            stopped["result"]["editor"]["fingerprint"]
            == first["result"]["editor"]["fingerprint"]
        )
        jobs.timeout = 0.001
        manager.command(
            project_id,
            1,
            str(uuid.uuid4()),
            {"type": "regenerate", "manual": [[0.2, 0.1, 0.1, 0.1]], "disabled": []},
        )
        timed_out = ready(manager, project_id)
        assert timed_out["revision"] == 1 and "time limit" in timed_out["error"]
        jobs.timeout = 300
        manager.command(project_id, 1, str(uuid.uuid4()), {"type": "undo"})
        project.pending = str(uuid.uuid4())
        expired = ready(manager, project_id)
        assert expired["revision"] == 1 and "expired" in expired["error"]


@pytest.mark.parametrize("fault", ["quota", "copy"])
def test_failed_commit_is_atomic(tmp_path, monkeypatch, fault):
    with managers(tmp_path) as (jobs, manager):
        project_id, first = opened(manager)
        manager.command(
            project_id,
            1,
            str(uuid.uuid4()),
            {"type": "merge", "region": 0, "target": 1},
        )
        if fault == "quota":
            monkeypatch.setattr(projects, "TOTAL_BYTES", 1)
        else:

            def fail(*args, **kwargs):
                raise OSError("simulated full disk")

            monkeypatch.setattr(projects.shutil, "copyfile", fail)
        failed = ready(manager, project_id)
        assert failed["revision"] == 1 and failed["error"]
        assert (
            failed["result"]["editor"]["fingerprint"]
            == first["result"]["editor"]["fingerprint"]
        )
        assert manager.reserved_bytes == 0


def test_history_count_and_byte_budget_prune_with_notice(monkeypatch):
    history = document()
    monkeypatch.setattr(projects, "MAX_ACTIONS", 3)
    for i in range(7):
        history = change_document(
            history, {"type": "number", "region": 0, "x": 40 + i, "y": 90}
        )
    assert len(history["states"]) == 4 and history["pruned"] == 4
    # Changing labels creates unique assets; a small test budget keeps the last checkpoint.
    history = document()
    monkeypatch.setattr(projects, "MAX_ACTIONS", 100)
    monkeypatch.setattr(project_io, "MAX_HISTORY", 180 * 240)
    for color in [2, 3, 1, 2]:
        history = change_document(
            history, {"type": "recolor", "region": 0, "color": color}
        )
    assert history["pruned"] > 0
    before = fingerprint(history["states"][history["cursor"] - 1])
    undo = change_document(history, {"type": "undo"})
    assert fingerprint(undo["states"][undo["cursor"]]) == before


def test_project_cap_and_shutdown_attempt_every_owned_project(tmp_path, monkeypatch):
    with managers(tmp_path) as (jobs, manager):
        manager.max_projects = 2
        first, _ = opened(manager)
        second, _ = opened(manager)
        with pytest.raises(JobCapacityError):
            manager.create(pack_document(document()), str(uuid.uuid4()))
        original = manager.close_project
        visited = []

        def close(project_id):
            visited.append(project_id)
            if project_id == first:
                raise OSError("simulated locked directory")
            return original(project_id)

        monkeypatch.setattr(manager, "close_project", close)
        with pytest.raises(ExceptionGroup):
            manager.close()
        assert visited == [first, second]
        monkeypatch.setattr(manager, "close_project", original)
        manager.closed = False


def test_forged_archive_header_is_rejected_before_pixel_decode(monkeypatch):
    data = pack_document(document())

    def no_decode(*args, **kwargs):
        raise AssertionError("Pixel decoding must not run for oversized archives.")

    monkeypatch.setattr(project_io, "MAX_EXPANDED", 1)
    monkeypatch.setattr(project_io, "decode_png", no_decode)
    with pytest.raises(ValueError, match="oversized"):
        project_io.unpack_document(data)


def test_unknown_command_and_saved_number_geometry_are_rejected():
    from editing import validate_numbers

    for command in [
        {"type": []},
        {"type": "merge", "region": 0, "target": 1, "extra": True},
    ]:
        with pytest.raises(ValueError, match="command"):
            change_document(document(), command)
    state = document()["states"][0]
    state["overrides"] = [{"anchor": [40, 90], "center": [0, 0]}]
    with pytest.raises(ValueError, match="saved manual"):
        validate_numbers(state)


def test_cancellation_discards_completed_candidate_before_commit(tmp_path):
    with managers(tmp_path) as (jobs, manager):
        project_id, first = opened(manager)
        manager.command(
            project_id,
            1,
            str(uuid.uuid4()),
            {"type": "merge", "region": 0, "target": 1},
        )
        pending = manager._project(project_id).pending
        wait_for(
            lambda: jobs.snapshot(pending), lambda state: state["status"] == "Complete"
        )
        assert manager.cancel(project_id) == {"status": "Stopped"}
        assert manager.cancel(project_id) == {"status": "Stopped"}
        cancelled = manager.snapshot(project_id)
        assert cancelled["revision"] == 1 and not cancelled["pending"]
        assert (
            cancelled["result"]["editor"]["fingerprint"]
            == first["result"]["editor"]["fingerprint"]
        )


def test_project_body_limits_and_local_origin_precede_parsing(monkeypatch):
    import server
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "MAX_BUNDLE", 1024)
    with TestClient(app=server.app, base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/api/projects/open",
                content=b"bad",
                headers={"Content-Length": str(258 * 1024)},
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/api/projects/open",
                content=b"bad",
                headers={"Origin": "https://example.com"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/projects/{uuid.uuid4()}/commands", content=b"x" * 65537
            ).status_code
            == 413
        )
        assert not server.app.state.projects.projects
