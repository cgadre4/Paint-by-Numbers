"""Regression coverage for the browser-only 1.2 release contract."""
import asyncio
import io
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms
from fastapi.testclient import TestClient
from pypdf import PdfReader

import pipeline
from canvas_size import canvas_metadata
from exports import make_export
from jobs import JobManager, JobCapacityError
from server import app, UploadLimitMiddleware
from tests.conftest import sample_image
from tests.workers import waiting_worker


def test_local_host_origin_policy_precedes_upload():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get('/api/hardware-status').status_code == 200
        assert client.get('/', headers={'Host': 'untrusted.example'}).status_code == 403
        for headers in ({'Origin': 'https://untrusted.example'}, {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}):
            assert client.post('/api/process/start', headers=headers).status_code == 403
        assert client.post('/api/process/start', headers={'Origin': 'http://127.0.0.1'}).status_code == 422


def test_cancel_before_start_and_idempotency(tmp_path, params):
    manager = JobManager(root=tmp_path, worker=waiting_worker)
    try:
        cancelled, active = str(uuid.uuid4()), str(uuid.uuid4())
        manager.cancel_request(cancelled)
        with pytest.raises(JobCapacityError, match='cancelled'):
            manager.start(sample_image(), params, cancelled)
        assert manager.start(sample_image(), params, active) == active
        assert manager.start(sample_image(), params, active) == active
        assert len(manager.jobs) == 1
        manager.cancel_request(active)
        assert manager.jobs[active].process is None
    finally:
        manager.close()


def test_cleanup_failure_does_not_leak_other_workers(tmp_path, params, monkeypatch):
    manager = JobManager(root=tmp_path, worker=waiting_worker)
    first = manager.start(sample_image(), params)
    second = manager.start(sample_image(), params)
    second_process = manager.jobs[second].process
    original = manager._remove_directory
    def fail_once(directory):
        if directory.name == first:
            raise PermissionError('Simulated locked directory')
        original(directory)
    monkeypatch.setattr(manager, '_remove_directory', fail_once)
    with pytest.raises(ExceptionGroup):
        manager.close()
    assert manager.closed
    assert not manager.manager._process.is_alive()
    with pytest.raises(ValueError, match='closed'):
        second_process.is_alive()
    assert not (manager.root / second).exists()
    original(manager.root / first)
    manager.root.rmdir()


def test_half_up_dimensions_and_restricted_decoder(params, monkeypatch):
    assert pipeline.scaled_dimensions(5, 25, .1) == (1, 3)
    original = Image.open
    seen = []
    def guarded(*args, **kwargs):
        seen.append(set(kwargs['formats']))
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', guarded)
    pipeline.decode_image(sample_image(), params)
    assert seen == [pipeline.SUPPORTED_FORMATS]


def test_color_profile_conversion_and_rejection(params):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    data = io.BytesIO()
    Image.new('RGB', (20, 20), (74, 150, 200)).save(data, format='PNG', icc_profile=profile)
    assert pipeline.decode_image(data.getvalue(), params).getpixel((0, 0)) == (74, 150, 200)
    data = io.BytesIO()
    Image.new('RGB', (20, 20)).save(data, format='PNG', icc_profile=b'invalid profile')
    with pytest.raises(pipeline.InvalidImage, match='profile'):
        pipeline.decode_image(data.getvalue(), params)


@pytest.mark.parametrize('width,height', [(300, 200), (200, 300)])
def test_physical_pdf_dimensions_and_readability(width, height):
    result = {'template_image': pipeline.img_to_base64(np.full((height, width, 3), 255, np.uint8)),
              'image_dimensions': {'width': width, 'height': height},
              'canvas': canvas_metadata(width, height, {'canvas_width_cm': 40})}
    page = PdfReader(io.BytesIO(make_export(result, 'template', 'pdf'))).pages[0]
    assert float(page.mediabox.width) == pytest.approx(400 * 72 / 25.4, abs=.01)
    assert float(page.mediabox.height) == pytest.approx(400 * height / width * 72 / 25.4, abs=.01)
    path = Path(f'.local/test-artifacts/custom-{width}-{height}.pdf')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_export(result, 'template', 'pdf'))
    small = canvas_metadata(4000, 2000, {'canvas_width_cm': 10}, [{'box': [1, 1, 12, 16]}])
    assert small['minimum_number_height_mm'] < 2 and small['warnings']


def test_upload_admission_and_deadline():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def endpoint(scope, receive, send):
            raise AssertionError('Incomplete upload must not enter multipart parsing')
        middleware = UploadLimitMiddleware(endpoint, max_uploads=1, upload_timeout=.05)
        scope = {'type': 'http', 'path': '/api/process/start', 'headers': []}
        messages = []
        async def send(message):
            messages.append(message)
        async def stalled():
            entered.set()
            await release.wait()
        task = asyncio.create_task(middleware(scope, stalled, send))
        await entered.wait()
        await middleware(scope, stalled, send)
        await task
        assert [m['status'] for m in messages if m['type'] == 'http.response.start'] == [429, 408]
        assert middleware.active == 0
    asyncio.run(scenario())


def test_cropped_eye_filter_matches_full_frame():
    import cv2
    from facial import preserve_eye_pixels
    source = np.arange(100 * 120 * 3, dtype=np.uint8).reshape(100, 120, 3)
    for rect in [(0, 0, 20, 20), (25, 30, 60, 50), (100, 80, 20, 20)]:
        x, y, w, h = rect
        mask = np.zeros(source.shape[:2], bool)
        mask[y:y+h, x:x+w] = True
        filtered = np.zeros_like(source)
        actual = preserve_eye_pixels(source, filtered, mask)
        expected = cv2.bilateralFilter(source, 3, 25, 25)
        assert np.array_equal(actual[mask], expected[mask])
        assert not actual[~mask].any()


def test_style_changes_reuse_owned_color_analysis(tmp_path, params):
    from tests.conftest import wait_for
    manager = JobManager(root=tmp_path)
    try:
        data = sample_image()
        first = manager.start(data, params)
        result = wait_for(lambda: manager.snapshot(first), lambda s: s['status'] in {'Complete', 'Error'})
        assert result['status'] == 'Complete', result
        updated = dict(params, stroke_width=1, canvas_width_cm=40)
        second = manager.start(data, updated)
        result = wait_for(lambda: manager.snapshot(second), lambda s: s['status'] in {'Complete', 'Error'})
        assert result['status'] == 'Complete', result
        assert result['result']['diagnostics']['reused_color_analysis']
        assert result['result']['parameters']['stroke_width'] == 1
        assert result['result']['canvas']['width_mm'] == 400
        # Source deletion does not invalidate the linked immutable artifact.
        with manager.result_lease(second):
            manager.ttl = 0
            manager.reap()
            assert (manager.jobs[second].directory / 'clusters.npz').exists()
    finally:
        manager.close()


def test_missing_region_mask_matches_warning_and_contains_only_missing():
    labels = np.zeros((100, 100), np.int32)
    labels[20:22, 20:22] = 1
    mask = np.zeros(labels.shape, np.uint8)
    _, placements, count = pipeline.extract_contours_and_label(labels, 2, 1, 1, missing_mask=mask)
    assert count == 1 and np.count_nonzero(mask) == 4
    for placement in placements:
        x, y, w, h = placement['box']
        assert not mask[y:y+h, x:x+w].any()


def test_expiry_failure_does_not_block_other_jobs(tmp_path, params, monkeypatch):
    manager = JobManager(root=tmp_path, worker=waiting_worker)
    try:
        first = manager.start(sample_image(), params)
        second = manager.start(sample_image(), params)
        manager.stop(first)
        manager.stop(second)
        original = manager._remove_directory
        def fail_first(directory):
            if directory.name == first:
                raise PermissionError('Simulated locked directory')
            original(directory)
        monkeypatch.setattr(manager, '_remove_directory', fail_first)
        manager.ttl = 0
        manager.reap()
        assert first in manager.jobs and second not in manager.jobs
        monkeypatch.setattr(manager, '_remove_directory', original)
    finally:
        manager.close()


def test_manager_initialization_failure_shuts_down_owner(monkeypatch):
    from types import SimpleNamespace
    import jobs
    closed = []
    owner = SimpleNamespace(dict=lambda: {}, shutdown=lambda: closed.append(True))
    monkeypatch.setattr(jobs.mp, 'get_context', lambda _: SimpleNamespace(Manager=lambda: owner))
    def fail_root(**kwargs):
        raise PermissionError('No temporary disk access')
    monkeypatch.setattr(jobs.tempfile, 'mkdtemp', fail_root)
    with pytest.raises(PermissionError):
        JobManager()
    assert closed == [True]


def test_shutdown_waits_for_result_lease(tmp_path, params):
    import threading
    manager = JobManager(root=tmp_path, worker=waiting_worker)
    job_id = manager.start(sample_image(), params)
    manager.stop(job_id)
    with manager.result_lease(job_id):
        thread = threading.Thread(target=manager.close)
        thread.start()
        thread.join(.05)
        assert thread.is_alive()
        assert manager.jobs[job_id].directory.exists()
    thread.join(10)
    assert not thread.is_alive() and manager.closed
    assert not manager.root.exists()


def test_empty_multipart_body_does_not_wait_for_another_request():
    async def scenario():
        messages, receives = [], []
        async def receive():
            receives.append(True)
            if len(receives) > 1:
                raise AssertionError('An empty complete body must be replayed once')
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            messages.append(message)
        async def endpoint(scope, replay, send):
            message = await replay()
            assert message == {'type': 'http.request', 'body': b'', 'more_body': False}
        await UploadLimitMiddleware(endpoint)({'type': 'http', 'path': '/api/process/start', 'headers': []}, receive, send)
    asyncio.run(scenario())


def test_sampled_experiment_assigns_all_pixels_reproducibly():
    from benchmarks.cpu import sampled_quantization
    image = np.arange(64 * 80 * 3, dtype=np.uint8).reshape(64, 80, 3)
    first, centers = sampled_quantization(image, 8, 42, sample_limit=256)
    second, repeated = sampled_quantization(image, 8, 42, sample_limit=256)
    assert first.shape == image.shape[:2] and first.min() >= 0 and first.max() < 8
    assert centers.shape == (8, 3) and np.isfinite(centers).all()
    assert np.array_equal(first, second) and np.array_equal(centers, repeated)


def test_indexed_source_uses_area_downscale(params):
    pixels = (np.indices((20, 20)).sum(axis=0) % 2).astype(np.uint8)
    image = Image.fromarray(pixels).convert('P')
    image.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    data = io.BytesIO()
    image.save(data, format='PNG')
    decoded = pipeline.decode_image(data.getvalue(), dict(params, resolution_factor=.5), resize=True)
    assert decoded.size == (10, 10)
    assert decoded.getpixel((5, 5)) == (128, 128, 128)


def test_complete_worker_releases_capacity_before_exit(tmp_path, params):
    from tests.workers import delayed_complete_worker
    from tests.conftest import wait_for
    manager = JobManager(root=tmp_path, max_active=1, worker=delayed_complete_worker)
    try:
        first = manager.start(sample_image(), params)
        wait_for(lambda: manager.states[first]['status'], lambda status: status == 'Complete')
        assert manager.jobs[first].process.is_alive()
        second = manager.start(sample_image(), params)
        assert manager.jobs[first].process is None and second in manager.jobs
    finally:
        manager.close()
