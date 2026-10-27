import io
import json
import uuid
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from editing import (
    apply_edit,
    protection_mask,
    region_data,
    validate_numbers,
    pixel_boxes,
    rectangles,
)
from exports import image_bytes
from project_io import pack_document, unpack_document
from projects import change_document, fingerprint
from server import app
from settings import DEFAULTS
from tests.conftest import wait_for


def document():
    labels = np.zeros((180, 240), np.uint8)
    labels[:, 80:160] = 1
    labels[:, 160:] = 2
    centers = np.array([[30, 50, 70], [100, 120, 140], [220, 230, 240]], np.float32)
    state = dict(
        source=centers[labels].astype(np.uint8),
        raw=labels.copy(),
        labels=labels,
        centers=centers,
        params=dict(DEFAULTS, k=3),
        manual=[],
        detected=[],
        disabled=[],
        faces=0,
        overrides=[],
        title="Generated design",
    )
    return dict(states=[state], cursor=0, original=None, pruned=0)


def test_manual_boxes_never_create_empty_pixel_areas():
    with pytest.raises(ValueError, match="inside"):
        rectangles([[1, 0, 1e-30, 0.1]])
    with pytest.raises(ValueError, match="inside"):
        rectangles([[0, 1, 0.1, 1e-30]])
    assert pixel_boxes((100, 100), [[0.5, 0.5, 1e-30, 1e-30]]) == [[50, 50, 1, 1]]


def terminal(client, project_id):
    return wait_for(
        lambda: client.get(f"/api/projects/{project_id}").json(),
        lambda s: not s.get("pending"),
        timeout=30,
    )


def test_recolor_one_component_coalesces_but_does_not_run_cleanup():
    state = document()["states"][0]
    state["labels"][20:23, 20:23] = 2
    state["raw"] = state["labels"].copy()
    regions, _, _ = region_data(state)
    component = int(regions[21, 21])
    result = apply_edit(state, {"type": "recolor", "region": component, "color": 2})
    assert np.all(result["labels"][20:23, 20:23] == 1)
    assert np.all(result["labels"][:, 160:] == 2)
    assert state["labels"][21, 21] == 2


def test_merge_requires_shared_edge_and_protection_is_preserved():
    state = document()["states"][0]
    with pytest.raises(ValueError, match="neighbor"):
        apply_edit(state, {"type": "merge", "region": 0, "target": 2})
    result = apply_edit(state, {"type": "merge", "region": 0, "target": 1})
    assert np.all(result["labels"][:, :160] == 1)
    assert len(region_data(result)[1]) == 2
    state["manual"] = [[0.05, 0.05, 0.1, 0.1]]
    with pytest.raises(ValueError, match="protected"):
        apply_edit(state, {"type": "recolor", "region": 0, "color": 2})


def test_manual_number_full_geometry_and_protection():
    state = document()["states"][0]
    edited = apply_edit(state, {"type": "number", "region": 0, "x": 40, "y": 90})
    validate_numbers(edited)
    for x, y in [(0, 0), (80, 90), (200, 90)]:
        with pytest.raises(ValueError, match="complete readable"):
            apply_edit(state, {"type": "number", "region": 0, "x": x, "y": y})
    edited["manual"] = [[0.1, 0.4, 0.2, 0.2]]
    with pytest.raises(ValueError, match="saved manual"):
        validate_numbers(edited)


def test_recolor_preserves_manual_numbers_in_unchanged_neighbor():
    state = document()["states"][0]
    state = apply_edit(state, {"type": "number", "region": 1, "x": 120, "y": 90})
    result = apply_edit(state, {"type": "recolor", "region": 0, "color": 3})
    assert result["overrides"] == state["overrides"]
    validate_numbers(result)


def test_exact_undo_redo_and_new_branch_clear_redo():
    original = document()
    before = fingerprint(original["states"][0])
    edited = change_document(original, {"type": "merge", "region": 0, "target": 1})
    after = fingerprint(edited["states"][1])
    undo = change_document(edited, {"type": "undo"})
    assert fingerprint(undo["states"][undo["cursor"]]) == before
    redo = change_document(undo, {"type": "redo"})
    assert fingerprint(redo["states"][redo["cursor"]]) == after
    branch = change_document(undo, {"type": "recolor", "region": 0, "color": 3})
    with pytest.raises(ValueError, match="no action"):
        change_document(branch, {"type": "redo"})
    assert original["cursor"] == 0 and len(original["states"]) == 1


def test_regeneration_is_a_reversible_checkpoint():
    original = document()
    regenerated = change_document(
        original,
        {"type": "regenerate", "manual": [[0.02, 0.1, 0.1, 0.1]], "disabled": []},
    )
    state = regenerated["states"][1]
    mask = protection_mask(state)
    assert np.array_equal(state["labels"][mask], state["raw"][mask])
    assert not state["overrides"]
    restored = change_document(regenerated, {"type": "undo"})
    assert fingerprint(restored["states"][0]) == fingerprint(original["states"][0])


def test_bundle_preserves_exact_arrays_history_and_overrides():
    edited = change_document(
        document(), {"type": "number", "region": 0, "x": 40, "y": 90}
    )
    restored = unpack_document(pack_document(edited))
    assert restored["cursor"] == 1
    for before, after in zip(edited["states"], restored["states"], strict=True):
        assert fingerprint(before) == fingerprint(after)
        validate_numbers(after)


def mutate_bundle(data, change, extra=None):
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        manifest = json.loads(source.read("manifest.json"))
        change(manifest)
        for name in source.namelist():
            target.writestr(
                name,
                json.dumps(manifest) if name == "manifest.json" else source.read(name),
            )
        if extra:
            target.writestr(*extra)
    return output.getvalue()


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(format=999),
        lambda m: m.update(cursor=True),
        lambda m: m["snapshots"][0]["params"].update(k=33),
        lambda m: m["snapshots"][0].update(labels="../secret.png"),
        lambda m: m["snapshots"][0].update(centers=[[float("nan")] * 3] * 3),
        lambda m: m["snapshots"][0].update(manual=[[0, 0, 2, 1]]),
        lambda m: m["snapshots"][0].update(disabled=[0]),
        lambda m: m["snapshots"][0].update(
            overrides=[{"anchor": [0, 0], "center": [-1, 4]}]
        ),
    ],
)
def test_untrusted_bundle_validation(change):
    with pytest.raises(ValueError):
        unpack_document(mutate_bundle(pack_document(document()), change))


@pytest.mark.parametrize(
    "name", ["../outside.txt", "/absolute.png", "assets/unknown.npy", "manifest.json"]
)
def test_bundle_rejects_paths_unknown_and_duplicate_entries(name):
    with pytest.raises(ValueError):
        if name == "manifest.json":
            with pytest.warns(UserWarning, match="Duplicate name"):
                data = mutate_bundle(
                    pack_document(document()), lambda m: None, (name, b"bad")
                )
            unpack_document(data)
        else:
            unpack_document(
                mutate_bundle(pack_document(document()), lambda m: None, (name, b"bad"))
            )


def test_project_api_edit_save_reopen_and_independent_job_lifetime(tmp_path):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        project_id = str(uuid.uuid4())
        response = client.post(
            "/api/projects/open",
            data={"request_id": project_id},
            files={"file": ("sample.pbn", pack_document(document()))},
        )
        assert response.status_code == 202, response.text
        first = terminal(client, project_id)
        assert first["revision"] == 1 and not first["error"], first
        revision = f"/api/projects/{project_id}/revisions/1"
        regions = client.get(revision + "/regions?x=40&y=90").json()
        assert regions["region"] == 0 and [
            n["region"] for n in regions["neighbors"]
        ] == [1]
        command = dict(
            expected_revision=1,
            request_id=str(uuid.uuid4()),
            command={"type": "merge", "region": 0, "target": 1},
        )
        accepted = client.post(f"/api/projects/{project_id}/commands", json=command)
        assert accepted.status_code == 202, accepted.text
        assert (
            client.post(f"/api/projects/{project_id}/commands", json=command).json()
            == accepted.json()
        )
        second = terminal(client, project_id)
        assert second["revision"] == 2 and not second["error"], second
        stale = dict(command, request_id=str(uuid.uuid4()))
        assert (
            client.post(f"/api/projects/{project_id}/commands", json=stale).status_code
            == 409
        )
        latest = f"/api/projects/{project_id}/revisions/2"
        original_template = client.get(latest + "/assets/template.png").content
        for kind, fmt in [
            ("template", "png"),
            ("template", "pdf"),
            ("palette", "png"),
            ("palette", "pdf"),
            ("tiles", "pdf"),
        ]:
            assert client.get(latest + f"/export/{kind}.{fmt}").status_code == 200
        saved = client.get(latest + "/export/project.pbn").content
        assert unpack_document(saved)["cursor"] == 1
        # Job retention cannot expire the committed project directory.
        app.state.jobs.ttl = 0
        app.state.jobs.reap()
        assert client.get(latest + "/assets/template.png").content == original_template
        assert client.delete(f"/api/projects/{project_id}").status_code == 200
    with TestClient(app, base_url="http://127.0.0.1") as client:
        reopened_id = str(uuid.uuid4())
        client.post(
            "/api/projects/open",
            data={"request_id": reopened_id},
            files={"file": ("saved.pbn", saved)},
        )
        reopened = terminal(client, reopened_id)
        assert (
            reopened["result"]["editor"]["fingerprint"]
            == second["result"]["editor"]["fingerprint"]
        )
        image = client.get(reopened["result"]["template_image"]).content
        assert np.array_equal(
            np.array(Image.open(io.BytesIO(image))),
            np.array(Image.open(io.BytesIO(original_template))),
        )
        undo = dict(
            expected_revision=1, request_id=str(uuid.uuid4()), command={"type": "undo"}
        )
        client.post(f"/api/projects/{reopened_id}/commands", json=undo)
        undone = terminal(client, reopened_id)
        assert undone["revision"] == 2 and undone["result"]["editor"]["cursor"] == 0


def test_failed_edit_or_import_preserves_previous_revision():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        project_id = str(uuid.uuid4())
        client.post(
            "/api/projects/open",
            data={"request_id": project_id},
            files={"file": ("sample.pbn", pack_document(document()))},
        )
        first = terminal(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/commands",
            json=dict(
                expected_revision=1,
                request_id=str(uuid.uuid4()),
                command={"type": "number", "region": 0, "x": 0, "y": 0},
            ),
        )
        assert response.status_code == 202
        failed = terminal(client, project_id)
        assert failed["error"] and failed["revision"] == 1
        assert (
            failed["result"]["editor"]["fingerprint"]
            == first["result"]["editor"]["fingerprint"]
        )
        bad = str(uuid.uuid4())
        client.post(
            "/api/projects/open",
            data={"request_id": bad},
            files={"file": ("bad.pbn", b"not a bundle")},
        )
        assert terminal(client, bad)["error"]
        assert client.get(f"/api/projects/{project_id}").json()["revision"] == 1


def test_manual_protection_start_and_create_project(params):
    from tests.conftest import sample_image

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/process/start",
            data=dict(
                params, protect_eyes="false", manual_protection="[[0.05,0.1,0.1,0.1]]"
            ),
            files={"file": ("test.png", sample_image())},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        finished = wait_for(
            lambda: client.get(f"/api/process/status/{job_id}").json(),
            lambda r: r["status"] in {"Complete", "Error"},
        )
        assert finished["status"] == "Complete", finished
        assert finished["result"]["eye_protection"]["boxes"]
        project_id = str(uuid.uuid4())
        assert (
            client.post(
                "/api/projects", json={"job_id": job_id, "request_id": project_id}
            ).status_code
            == 202
        )
        project = terminal(client, project_id)
        assert project["result"], project
        assert project["result"]["editor"]["manual"] == [[0.05, 0.1, 0.1, 0.1]]
        assert project["result"]["editor"]["detected"] == []
        assert (
            image_bytes(finished["result"]["template_image"])
            == client.get(project["result"]["template_image"]).content
        )
