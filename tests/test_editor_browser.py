"""Real-browser editing, protection gestures, portable downloads and stale-response checks."""

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from playwright.sync_api import expect

from project_io import pack_document, unpack_document
from tests.test_editing import document
from tests.test_browser import browser, live_server, page, upload, complete  # noqa: F401

ARTIFACTS = Path(".local/test-artifacts/editor-1.4")


@pytest.fixture(autouse=True)
def cleanup_sessions(page):
    yield
    # Browser automation may destroy a context without delivering pagehide.
    for session in page.request.get("/api/projects").json():
        page.request.delete(f"/api/projects/{session['project_id']}")


def open_document(page, data=None):
    expect(page.locator('#openProjectBtn')).to_be_enabled()
    page.locator("#projectFileInput").set_input_files(
        {
            "name": "test.pbn",
            "mimeType": "application/octet-stream",
            "buffer": pack_document(document()) if data is None else data,
        }
    )
    expect(page.locator("#editorPanel")).to_be_visible(timeout=30000)
    expect(page.locator("#saveProjectBtn")).to_be_enabled(timeout=30000)


def project_state(page):
    href = page.locator("#downloadTemplatePNG").get_attribute("href")
    project_id = href.split("/")[3]
    return page.request.get(f"/api/projects/{project_id}").json()


def wait_revision(page, number):
    expect(page.locator("#downloadTemplatePNG")).to_have_attribute(
        "href", __import__("re").compile(f"/revisions/{number}/export/")
    )
    expect(page.locator("#cancelEditBtn")).to_be_hidden(timeout=30000)


def test_editor_merge_recolor_number_undo_redo_save_reopen(page):
    open_document(page)
    initial = project_state(page)["result"]["editor"]["fingerprint"]
    page.locator("#regionList").select_option("0")
    expect(page.locator("#regionDescription")).to_contain_text("Region 1 · palette 1")
    page.locator("#numberX").fill("40")
    page.locator("#numberY").fill("90")
    page.locator("#placeNumberBtn").click()
    wait_revision(page, 2)
    assert project_state(page)["result"]["label_placements"][0]["anchor"] == [40, 90]
    page.locator("#regionList").select_option("0")
    expect(page.locator("#mergeRegionBtn")).to_be_enabled()
    page.locator("#mergeRegionBtn").click()
    wait_revision(page, 3)
    merged = project_state(page)["result"]["editor"]["fingerprint"]
    page.locator("#undoEditBtn").click()
    wait_revision(page, 4)
    page.locator("#redoEditBtn").click()
    wait_revision(page, 5)
    assert project_state(page)["result"]["editor"]["fingerprint"] == merged
    page.locator("#regionList").select_option("0")
    page.locator("#regionColor").select_option("3")
    page.locator("#recolorRegionBtn").click()
    wait_revision(page, 6)
    assert project_state(page)["result"]["editor"]["region_count"] == 1
    with page.expect_download() as download:
        page.locator("#saveProjectBtn").click()
    data = Path(download.value.path()).read_bytes()
    saved = unpack_document(data)
    assert saved["cursor"] == 3 and len(saved["states"]) == 4
    expected = project_state(page)["result"]["editor"]["fingerprint"]
    expect(page.locator("#projectBadge")).to_have_text("Saved project")
    page.locator("#closeProjectBtn").click()
    expect(page.locator("#editorPanel")).to_be_hidden()
    open_document(page, data)
    assert project_state(page)["result"]["editor"]["fingerprint"] == expected != initial
    page.locator("#undoEditBtn").click()
    wait_revision(page, 2)
    assert project_state(page)["result"]["editor"]["region_count"] == 2
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.locator("#historyDetails").evaluate("(node)=>node.open=true")
    page.screenshot(path=str(ARTIFACTS / "desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".main-content").scroll_into_view_if_needed()
    page.screenshot(path=str(ARTIFACTS / "mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_manual_protection_before_processing_and_regeneration_undo(page):
    upload(page)
    page.locator("#protectTool").click()
    for name, value in [
        ("eyeX", "5"),
        ("eyeY", "10"),
        ("eyeWidth", "10"),
        ("eyeHeight", "10"),
    ]:
        page.locator("#" + name).fill(value)
    page.locator("#applyEyeBoxBtn").click()
    expect(page.locator("#protectionList")).to_have_value("0")
    # Draft undo/redo is available before the first processing job.
    page.locator("#undoEditBtn").click()
    expect(page.locator("#protectionList option")).to_have_count(1)
    page.locator("#redoEditBtn").click()
    expect(page.locator("#protectionList option")).to_have_count(2)
    complete(page)
    page.locator("#finishDesignBtn").click()
    expect(page.locator("#editorPanel")).to_be_visible(timeout=30000)
    expect(page.locator("#cancelEditBtn")).to_be_hidden(timeout=30000)
    baseline = project_state(page)["result"]["editor"]["fingerprint"]
    page.locator("#protectTool").click()
    page.locator("#protectionList").select_option("0")
    page.locator("#eyeX").fill("30")
    page.locator("#applyEyeBoxBtn").click()
    expect(page.locator("#saveProjectBtn")).to_be_disabled()
    page.locator("#regenerateProtectionBtn").click()
    wait_revision(page, 2)
    result = project_state(page)["result"]
    assert result["editor"]["manual"][0][0] == 0.3
    assert result["eye_protection"]["boxes"]
    page.locator("#undoEditBtn").click()
    wait_revision(page, 3)
    assert project_state(page)["result"]["editor"]["fingerprint"] == baseline


def test_region_selection_under_zoom_and_invalid_number_keeps_revision(page):
    open_document(page)
    page.locator("#viewChoice").select_option("template")
    page.locator("#zoomSlider").fill("150")
    page.locator("#zoomSlider").dispatch_event("input")
    page.locator("#selectTool").click()
    # Browser coordinates include both object-fit letterboxing and CSS transforms.
    point = page.locator("#templateCanvas").evaluate("""canvas => {
        const r=canvas.getBoundingClientRect(), s=Math.min(r.width/canvas.width,r.height/canvas.height);
        return {x:r.left+(r.width-canvas.width*s)/2+40*s,y:r.top+(r.height-canvas.height*s)/2+90*s};
    }""")
    page.mouse.click(point["x"], point["y"])
    expect(page.locator("#regionDescription")).to_contain_text("Region 1 · palette 1")
    page.locator("#numberX").fill("0")
    page.locator("#numberY").fill("0")
    page.locator("#placeNumberBtn").click()
    expect(page.locator("#statusMessage")).to_contain_text(
        "previous design is unchanged", timeout=30000
    )
    expect(page.locator("#cancelEditBtn")).to_be_hidden()
    assert project_state(page)["revision"] == 1
    expect(page.locator("#refreshProjectBtn")).to_be_hidden()


def test_failed_import_preserves_open_project_and_exports_are_clean(page):
    open_document(page)
    first = project_state(page)
    href = page.locator("#downloadTemplatePNG").get_attribute("href")
    before = page.request.get(href).body()
    page.locator("#regionList").select_option("0")
    expect(page.locator("#regionDescription")).to_contain_text("Region 1")
    page.locator("#showMissing").check()
    page.locator("#showEyes").check()
    page.locator("#projectFileInput").set_input_files(
        {
            "name": "broken.pbn",
            "mimeType": "application/octet-stream",
            "buffer": b"broken",
        }
    )
    expect(page.locator("#statusMessage")).to_contain_text(
        "Could not open project", timeout=30000
    )
    assert project_state(page)["project_id"] == first["project_id"]
    assert page.request.get(href).body() == before
    page.locator("#printReadyBtn").click()
    displayed = page.locator("#templateCanvas").evaluate(
        "canvas=>canvas.toDataURL('image/png')"
    )
    from exports import image_bytes

    assert np.array_equal(
        np.array(Image.open(io.BytesIO(image_bytes(displayed))).convert("RGB")),
        np.array(Image.open(io.BytesIO(before)).convert("RGB")),
    )


def test_protection_gesture_move_resize_and_pointer_cancel(page):
    upload(page)
    page.locator("#protectTool").click()
    canvas = page.locator("#sourceCanvas")
    # Dispatch pointer coordinates from the actual content bounds.
    box = canvas.bounding_box()
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    page.mouse.move(x + w * 0.2, y + h * 0.2)
    page.mouse.down()
    page.mouse.move(x + w * 0.4, y + h * 0.4, steps=4)
    page.mouse.up()
    expect(page.locator("#protectionList option")).to_have_count(2)
    old_x = float(page.locator("#eyeX").input_value())
    page.mouse.move(x + w * 0.3, y + h * 0.3)
    page.mouse.down()
    page.mouse.move(x + w * 0.35, y + h * 0.35, steps=4)
    page.mouse.up()
    assert float(page.locator("#eyeX").input_value()) > old_x
    before = page.locator("#eyeWidth").input_value()
    page.mouse.move(x + w * 0.7, y + h * 0.7)
    page.mouse.down()
    canvas.dispatch_event("pointercancel", {"pointerId": 1})
    page.mouse.up()
    assert page.locator("#eyeWidth").input_value() == before


def test_lost_edit_response_is_idempotent_and_late_response_cannot_restore_reset(page):
    open_document(page)
    page.locator("#regionList").select_option("0")
    expect(page.locator("#mergeRegionBtn")).to_be_enabled()

    def lost_response(route):
        route.fetch()
        route.abort()

    page.route("**/api/projects/*/commands", lost_response, times=1)
    page.locator("#mergeRegionBtn").click()
    wait_revision(page, 2)
    assert project_state(page)["result"]["editor"]["history"] == [
        "Generated design",
        "Merged adjacent regions",
    ]
    page.on("dialog", lambda dialog: dialog.accept())

    def delayed_response(route):
        response = route.fetch()
        page.locator("#resetBtn").click()
        route.fulfill(response=response)

    page.route("**/api/projects/*/commands", delayed_response, times=1)
    page.locator("#undoEditBtn").click()
    expect(page.locator("#editorPanel")).to_be_hidden()
    expect(page.locator("#resultsContainer")).to_be_hidden()
    expect(page.locator("#sourcePlaceholder")).to_be_visible()


def test_unsaved_reset_can_be_cancelled_and_keyboard_undo_works(page):
    open_document(page)
    page.locator("#regionList").select_option("0")
    expect(page.locator("#recolorRegionBtn")).to_be_disabled()
    page.locator("#regionColor").select_option("2")
    page.locator("#recolorRegionBtn").click()
    wait_revision(page, 2)

    def dismiss(dialog):
        dialog.dismiss()

    page.on("dialog", dismiss)
    page.locator("#resetBtn").click()
    expect(page.locator("#editorPanel")).to_be_visible()
    page.locator("#templateCanvas").focus()
    page.keyboard.press("Control+z")
    wait_revision(page, 3)
    expect(page.locator("#projectBadge")).to_have_text("Saved project")


def test_navigation_closes_session_and_sessions_list_can_close_abandoned_project(page):
    open_document(page)
    project_id = project_state(page)["project_id"]
    with page.expect_response(f"**/api/projects/{project_id}/close"):
        # A bfcache entry stays alive intentionally; exercise a final pagehide.
        page.evaluate(
            'window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: false}))'
        )
    page.goto("/?reopened=1")
    assert all(
        s["project_id"] != project_id for s in page.request.get("/api/projects").json()
    )
    # Create an independent session as if another tab had crashed.
    import uuid

    orphan = str(uuid.uuid4())
    page.request.post(
        "/api/projects/open",
        multipart={
            "request_id": orphan,
            "file": {
                "name": "orphan.pbn",
                "mimeType": "application/octet-stream",
                "buffer": pack_document(document()),
            },
        },
    )
    page.locator("#sessionDetails").evaluate("(node)=>node.open=true")
    page.locator("#refreshSessionsBtn").click()
    expect(page.locator("#projectSessions")).to_contain_text(orphan[:8])
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#projectSessions button").click()
    expect(page.locator("#projectSessions")).to_have_text("No projects are open.")
