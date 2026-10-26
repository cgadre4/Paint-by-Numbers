import base64
import io
import uuid
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

import pipeline
from exports import make_export
from jobs import JobManager, JobCapacityError, JobNotFound
from pipeline import InvalidImage, extract_contours_and_label, normalize_image
from server import app
from tests.conftest import sample_image, wait_for
from tests.workers import waiting_worker, crashing_worker, error_worker


@pytest.fixture(scope='module')
def client():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def submit(client, params, data=None):
    return client.post('/api/process/start', data=params,
                       files={'file': ('image.png', sample_image() if data is None else data, 'image/png')})


@pytest.mark.parametrize('k,factor', [(3, 0.5), (32, 1)])
def test_processing_contract_and_all_exports(client, params, k, factor):
    params.update(k=k, resolution_factor=factor)
    response = submit(client, params)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    state = wait_for(lambda: client.get(f'/api/process/status/{job_id}').json(),
                     lambda value: value['status'] in {'Complete', 'Error'})
    assert state['status'] == 'Complete', state
    result = state['result']
    assert result['parameters']['color_space'] == 'lab'
    assert result['eye_protection']['enabled'] is True
    assert result['image_dimensions'] == {'width': round(256 * factor), 'height': round(160 * factor)}
    assert [color['id'] for color in result['palette']] == list(range(1, k + 1))
    for color in result['palette']:
        assert color['hex'] == '#{:02x}{:02x}{:02x}'.format(*color['rgb'])
    labels = np.array(Image.open(io.BytesIO(base64.b64decode(result['label_map'].split(',')[1]))))
    assert labels.min() >= 0 and labels.max() < k
    expected_shape = (round(160 * factor), round(256 * factor), 3)
    for name in ('processed_image', 'template_image'):
        image = np.array(Image.open(io.BytesIO(base64.b64decode(result[name].split(',')[1]))))
        assert image.shape == expected_shape
    artifacts = __import__('pathlib').Path('.local/test-artifacts')
    artifacts.mkdir(parents=True, exist_ok=True)
    for kind in ('template', 'palette'):
        for format_ in ('png', 'pdf'):
            export = client.get(f'/api/process/export/{job_id}/{kind}.{format_}')
            assert export.status_code == 200
            assert f'{kind}.{format_}' in export.headers['content-disposition']
            if format_ == 'pdf':
                reader = PdfReader(io.BytesIO(export.content))
                assert len(reader.pages) == 1
                if kind == 'palette':
                    text = reader.pages[0].extract_text()
                    assert all(f'{color["id"]}: {color["hex"].upper()}' in text for color in result['palette'])
            else:
                with Image.open(io.BytesIO(export.content)) as image:
                    image.verify()
            if k == 32:
                (artifacts / f'{kind}.{format_}').write_bytes(export.content)
    wait_for(lambda: (app.state.jobs.reap(), app.state.jobs.jobs[job_id].process)[1], lambda p: p is None)
    assert not (app.state.jobs.jobs[job_id].directory / 'input.png').exists()
    # Stopping a completed job should preserve its downloadable result.
    assert client.post(f'/api/process/stop/{job_id}').json()['status'] == 'Complete'


@pytest.mark.parametrize('field,value', [('k', 2), ('k', 33), ('k', 'abc'), ('k', 3.5),
    ('resolution_factor', 0), ('resolution_factor', 'nan'), ('font_scale', 'inf'),
    ('bilateral_diameter', 16), ('sigma_color', 0), ('area_min', 0), ('stroke_width', 5),
    ('color_space', 'unknown'), ('protect_eyes', 'perhaps'), ('canvas_width_cm', 'inf'),
    ('canvas_width_cm', -1), ('canvas_width_cm', 201), ('canvas_width_cm', 4.9), ('request_id', 'invalid'), ('unknown', 'value')])
def test_invalid_parameters_do_not_create_jobs(client, params, field, value):
    params[field] = value
    before = set(app.state.jobs.jobs)
    assert submit(client, params).status_code == 422
    assert set(app.state.jobs.jobs) == before


@pytest.mark.parametrize('data', [b'', b'not an image', b'\x89PNG\r\n\x1a\ntruncated'])
def test_invalid_upload(client, params, data):
    response = submit(client, params, data)
    assert response.status_code == 202
    state = wait_for(lambda: client.get(f"/api/process/status/{response.json()['job_id']}").json(), lambda value: value['status'] == 'Error')
    assert state['error_code'] == 'invalid_image'


def test_missing_field_and_tiny_input(client, params):
    del params['k']
    assert submit(client, params).status_code == 422
    params['k'] = 32
    response = submit(client, params, sample_image(2, 2))
    assert response.status_code == 202
    state = wait_for(lambda: client.get(f"/api/process/status/{response.json()['job_id']}").json(), lambda value: value['status'] == 'Error')
    assert state['error_code'] == 'invalid_image'


def test_upload_size_limit(client, params):
    response = submit(client, params, b'x' * (pipeline.MAX_UPLOAD_BYTES + 1))
    assert response.status_code == 413
    # Content-length guard runs before multipart parsing.
    response = client.post('/api/process/start', headers={'content-length': str(30 * 1024 * 1024)}, content=b'')
    assert response.status_code == 413


def test_chunked_body_limit(client):
    response = client.post('/api/process/start', content=iter([b'x' * 1024 * 1024] * 22),
        headers={'content-type': 'multipart/form-data; boundary=test'})
    assert response.status_code == 413


def test_pixel_limits_and_normalization(params, monkeypatch):
    monkeypatch.setattr(pipeline, 'MAX_INPUT_PIXELS', 100)
    with pytest.raises(InvalidImage, match='40 million'):
        normalize_image(sample_image(11, 10), params)
    monkeypatch.setattr(pipeline, 'MAX_INPUT_PIXELS', 1000)
    monkeypatch.setattr(pipeline, 'MAX_PROCESS_PIXELS', 100)
    with pytest.raises(InvalidImage, match='Resolution Factor'):
        normalize_image(sample_image(11, 10), params)
    transparent = io.BytesIO()
    Image.new('RGBA', (10, 10), (255, 0, 0, 0)).save(transparent, format='PNG')
    image = Image.open(io.BytesIO(normalize_image(transparent.getvalue(), params)))
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_invalid_and_unknown_jobs(client):
    assert client.get('/api/process/status/invalid').status_code == 422
    missing = str(uuid.uuid4())
    for method, url in [('get', f'/api/process/status/{missing}'),
                        ('post', f'/api/process/stop/{missing}'),
                        ('get', f'/api/process/export/{missing}/template.pdf')]:
        assert getattr(client, method)(url).status_code == 404


def test_labels_stay_in_concave_and_holed_regions():
    labels = np.zeros((180, 240), np.int32)
    cv2.rectangle(labels, (20, 20), (100, 150), 1, -1)
    cv2.rectangle(labels, (55, 20), (100, 100), 0, -1)  # Concave L
    cv2.circle(labels, (170, 85), 60, 2, -1)
    cv2.circle(labels, (170, 85), 25, 0, -1)  # Hole
    template, placements, missing = extract_contours_and_label(labels, 3, 2, 1)
    assert placements and missing == 0
    for placement in placements:
        x, y, w, h = placement['box']
        assert x >= 0 and y >= 0 and x+w <= 240 and y+h <= 180
        assert np.all(labels[y:y+h, x:x+w] == placement['id'] - 1)
    assert template[85, 145].min() < 100  # Hole boundary is drawn.
    _, placements, missing = extract_contours_and_label(np.zeros((3, 3), np.int32), 1, 2, 1)
    assert not placements and missing == 1


@pytest.mark.parametrize('worker,expected', [(crashing_worker, 'unexpectedly'), (error_worker, 'Intentional')])
def test_worker_failure_is_reaped(tmp_path, params, worker, expected):
    with_manager = JobManager(root=tmp_path, worker=worker)
    try:
        job_id = with_manager.start(sample_image(), params)
        state = wait_for(lambda: with_manager.snapshot(job_id), lambda value: value['status'] == 'Error')
        assert expected in state['error']
        wait_for(lambda: (with_manager.reap(), with_manager.jobs[job_id].process)[1], lambda p: p is None)
        assert not (with_manager.jobs[job_id].directory / 'input.png').exists()
    finally:
        with_manager.close()
    assert not with_manager.root.exists()


def test_stop_timeout_expiry_capacity_and_shutdown(tmp_path, params):
    manager = JobManager(root=tmp_path, worker=waiting_worker, max_active=1)
    try:
        job_id = manager.start(sample_image(), params)
        with pytest.raises(JobCapacityError):
            manager.start(sample_image(), params)
        assert manager.stop(job_id)['status'] == 'Stopped'
        assert manager.stop(job_id)['status'] == 'Stopped'
        assert manager.jobs[job_id].process is None
        assert not (manager.jobs[job_id].directory / 'input.png').exists()
        manager.ttl = 0
        manager.reap()
        with pytest.raises(JobNotFound):
            manager.snapshot(job_id)
        manager.ttl = 900
        job_id = manager.start(sample_image(), params)
        manager.jobs[job_id].started -= manager.timeout + 1
        manager.reap()
        assert manager.snapshot(job_id)['status'] == 'Error'
        manager.start(sample_image(), params)
    finally:
        manager.close()
    assert not manager.root.exists()
    manager.close()  # Idempotent shutdown.


def test_retention_and_start_failure(tmp_path, params, monkeypatch):
    manager = JobManager(root=tmp_path, worker=waiting_worker, max_retained=1)
    try:
        first = manager.start(sample_image(), params)
        manager.stop(first)
        second = manager.start(sample_image(), params)
        manager.stop(second)
        manager.reap()
        assert list(manager.jobs) == [second]
        def broken_start(self):
            raise OSError('Simulated spawn failure')
        monkeypatch.setattr(type(manager.context.Process()), 'start', broken_start)
        before = set(manager.root.iterdir())
        with pytest.raises(OSError, match='spawn failure'):
            manager.start(sample_image(), params)
        assert set(manager.root.iterdir()) == before
        assert set(manager.states) == {second}
    finally:
        manager.close()


def test_capacity_and_export_before_completion(client, params, monkeypatch):
    manager = app.state.jobs
    monkeypatch.setattr(manager, 'max_active', 0)
    assert submit(client, params).status_code == 429
    monkeypatch.setattr(manager, 'max_active', 2)
    monkeypatch.setattr(manager, 'worker', waiting_worker)
    response = submit(client, params)
    assert response.status_code == 202
    job_id = response.json()['job_id']
    assert client.get(f'/api/process/export/{job_id}/template.pdf').status_code == 409
    assert client.post(f'/api/process/stop/{job_id}').json()['status'] == 'Stopped'
    assert client.get(f'/api/process/export/{job_id}/template.pdf').status_code == 409


def test_animation_and_orientation(params):
    frames = [Image.new('RGB', (32, 24), color) for color in ('red', 'blue')]
    output = io.BytesIO()
    frames[0].save(output, format='PNG', save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(InvalidImage, match='animation'):
        normalize_image(output.getvalue(), params)
    output = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    frames[0].save(output, format='JPEG', exif=exif)
    normalized = Image.open(io.BytesIO(normalize_image(output.getvalue(), params)))
    assert normalized.size == (24, 32)


@pytest.mark.parametrize('width,height', [(180, 260), (260, 180)])
def test_pdf_orientation_and_margins(width, height):
    labels = np.zeros((height, width), np.int32)
    labels[height//3:2*height//3, width//3:2*width//3] = 1
    template, _, _ = extract_contours_and_label(labels, 2, 1, 0.5)
    result = {'template_image': pipeline.img_to_base64(template),
              'image_dimensions': {'width': width, 'height': height}}
    pdf = make_export(result, 'template', 'pdf')
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    assert (float(page.mediabox.width) > float(page.mediabox.height)) == (width > height)
    # The PDF image is fitted with an affine transform inside the A4 page margins.
    transforms = [args for args, op in page.get_contents().operations if op == b'cm' and float(args[0]) != 1]
    assert len(transforms) == 1
    a, _, _, d, x, y = map(float, transforms[0])
    assert min(x, y) >= 28.34
    assert x+a <= float(page.mediabox.width)-28.34
    assert y+d <= float(page.mediabox.height)-28.34
    if width < height:
        path = __import__('pathlib').Path('.local/test-artifacts/template-portrait.pdf')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf)


@pytest.mark.parametrize('reverse_completion', [False, True])
def test_retention_uses_completion_order_when_clock_ticks_tie(tmp_path, params, monkeypatch, reverse_completion):
    manager = JobManager(root=tmp_path, worker=waiting_worker, max_retained=1)
    try:
        identifiers = iter([uuid.UUID(int=2), uuid.UUID(int=1)])
        monkeypatch.setattr('jobs.uuid.uuid4', lambda: next(identifiers))
        monkeypatch.setattr('jobs.time', SimpleNamespace(monotonic=lambda: 100.0))
        first = manager.start(sample_image(), params)
        second = manager.start(sample_image(), params)
        older, newer = (second, first) if reverse_completion else (first, second)
        manager.stop(older)
        manager.stop(newer)
        assert manager.jobs[older].finished == manager.jobs[newer].finished
        manager.reap()
        assert list(manager.jobs) == [newer]
        assert set(manager.states) == {newer}
        assert not (manager.root / older).exists()
    finally:
        manager.close()


def test_omitted_color_options_use_perceptual_and_eye_defaults(client, params):
    params.pop('color_space')
    params.pop('protect_eyes')
    response = submit(client, params)
    assert response.status_code == 202
    job_id = response.json()['job_id']
    state = wait_for(lambda: client.get(f'/api/process/status/{job_id}').json(),
                     lambda value: value['status'] in {'Complete','Error'})
    assert state['status'] == 'Complete', state
    assert state['result']['parameters']['color_space'] == 'lab'
    assert state['result']['parameters']['protect_eyes'] is True
