import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader

from tests.conftest import DEFAULTS, sample_image


@pytest.fixture(scope='module')
def live_server():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    artifacts = Path('.local/test-artifacts')
    artifacts.mkdir(parents=True, exist_ok=True)
    import threading
    import uvicorn
    server = uvicorn.Server(uvicorn.Config('server:app', host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            if not thread.is_alive():
                raise AssertionError('Test server exited')
            time.sleep(0.1)
        else:
            raise AssertionError('Test server did not become ready')
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive(), 'Server did not shut down gracefully'



@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        channel = os.environ.get('PBN_BROWSER_CHANNEL')
        if not channel and Path('C:/Program Files/Google/Chrome/Application/chrome.exe').exists():
            channel = 'chrome'
        browser = playwright.chromium.launch(headless=True, channel=channel)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(base_url=live_server, viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server)
    yield page
    context.close()
    assert not errors, errors


def upload(page, data=None, name='sample.png'):
    page.locator('#fileInput').set_input_files({'name': name, 'mimeType': 'image/png',
                                               'buffer': sample_image() if data is None else data})
    expect(page.locator('#processBtn')).to_be_enabled()


def set_slider(page, name, value):
    page.locator(name).fill(str(value))


def complete(page):
    page.locator('#processBtn').click()
    expect(page.locator('#resultsContainer')).to_be_visible(timeout=30000)
    expect(page.locator('#stopBtn')).to_be_hidden()


def canvas_hash(page):
    return page.locator('#templateCanvas').evaluate('(canvas) => canvas.toDataURL()')


def test_real_upload_processing_palette_downloads_print_and_navigation(page):
    expect(page.locator('#resultsContainer')).to_be_hidden()
    # Clicking anywhere on the dropzone opens the file picker once.
    with page.expect_file_chooser() as chooser:
        page.locator('#dropzone').click()
    chooser.value.set_files({'name': 'sample.png', 'mimeType': 'image/png', 'buffer': sample_image()})
    expect(page.locator('#processBtn')).to_be_enabled()
    set_slider(page, '#kSlider', 32)
    complete(page)
    expect(page.locator('.palette-swatch')).to_have_count(32)
    expect(page.locator('.palette-swatch').last).to_have_text('32')
    baseline = canvas_hash(page)
    page.locator('.palette-swatch').first.click()
    assert canvas_hash(page) != baseline
    page.locator('.palette-swatch').first.click()
    assert canvas_hash(page) == baseline
    set_slider(page, '#zoomSlider', 150)
    assert '1.5' in page.locator('#sourceCanvas').get_attribute('style')
    canvas = page.locator('#sourceCanvas')
    bounds = canvas.bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down(); page.mouse.move(x + 30, y + 20); page.mouse.up()
    assert '30px, 20px' in canvas.get_attribute('style')
    page.locator('#resetTransformBtn').click()
    expect(page.locator('#zoomBadge')).to_have_text('100%')
    for kind in ('Template', 'Palette'):
        for format_ in ('PNG', 'PDF'):
            with page.expect_download() as download:
                page.locator(f'#download{kind}{format_}').click()
            path = Path('.local/test-artifacts') / f'browser-{kind.lower()}.{format_.lower()}'
            download.value.save_as(path)
            if format_ == 'PDF':
                assert len(PdfReader(path).pages) == 1
            else:
                with Image.open(path) as image:
                    image.verify()
    page.locator('.palette-swatch').first.click()
    page.locator('#printReadyBtn').click()
    expect(page.locator('.source-viewer')).to_be_hidden()
    assert canvas_hash(page) == baseline
    page.emulate_media(media='print')
    expect(page.locator('.app-container')).to_be_hidden()
    expect(page.locator('#printTemplate')).to_be_visible()
    page.emulate_media(media='screen')
    page.locator('#printReadyBtn').click()
    expect(page.locator('.source-viewer')).to_be_visible()
    page.screenshot(path='.local/test-artifacts/app-desktop.png', full_page=True)


def test_reset_restores_submitted_defaults_and_new_upload_hides_results(page):
    upload(page)
    set_slider(page, '#kSlider', 12)
    set_slider(page, '#resolutionFactor', 0.5)
    complete(page)
    page.locator('#resetBtn').click()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    expect(page.locator('#processBtn')).to_be_disabled()
    expect(page.locator('#kSlider')).to_have_value('8')
    expect(page.locator('#resolutionFactor')).to_have_value('1')
    upload(page)
    with page.expect_request('**/api/process/start') as request:
        complete(page)
    job_id = page.locator('#downloadTemplatePNG').get_attribute('href').split('/')[-2]
    result = page.request.get(f'/api/process/status/{job_id}').json()['result']
    assert result['parameters'] == DEFAULTS
    upload(page, name='replacement.png')
    expect(page.locator('#resultsContainer')).to_be_hidden()
    expect(page.locator('.palette-swatch')).to_have_count(0)


def test_drop_upload_invalid_image_and_mobile(page):
    data = list(sample_image())
    page.locator('#dropzone').evaluate('''(zone, bytes) => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([new Uint8Array(bytes)], 'dropped.png', {type: 'image/png'}));
        zone.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: transfer}));
    }''', data)
    expect(page.locator('#processBtn')).to_be_enabled()
    page.locator('#fileInput').set_input_files({'name': 'broken.png', 'mimeType': 'image/png', 'buffer': b'broken'})
    expect(page.locator('#statusMessage')).to_contain_text('could not be decoded')
    expect(page.locator('#processBtn')).to_be_disabled()
    page.set_viewport_size({'width': 390, 'height': 844})
    upload(page)
    complete(page)
    assert page.evaluate('document.documentElement.scrollWidth') <= 390
    page.screenshot(path='.local/test-artifacts/app-mobile.png', full_page=True)


def test_cancel_pending_start_stops_returned_job_and_can_restart(page):
    # Return a real job ID only after reset, reproducing the pending /start race.
    upload(page)
    def delayed_start(route):
        response = route.fetch()
        page.locator('#resetBtn').click()
        route.fulfill(response=response)
    page.route('**/api/process/start', delayed_start, times=1)
    with page.expect_request('**/api/process/stop/*'):
        page.locator('#processBtn').click()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    expect(page.locator('#processBtn')).to_be_disabled()
    upload(page)
    complete(page)


def test_old_poll_cannot_restore_results_after_reset(page):
    upload(page)
    def stale_poll(route):
        response = route.fetch()
        page.locator('#resetBtn').click()
        try:
            route.fulfill(response=response)
        except Exception:
            pass  # The browser may already have aborted this obsolete request.
    page.route('**/api/process/status/*', stale_poll, times=1)
    page.locator('#processBtn').click()
    expect(page.locator('#sourcePlaceholder')).to_be_visible()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    expect(page.locator('#processBtn')).to_be_disabled()
    upload(page)
    complete(page)


def test_stop_running_job_then_process_again(page):
    upload(page, sample_image(1200, 800))
    with page.expect_response('**/api/process/start'):
        page.locator('#processBtn').click()
    with page.expect_request('**/api/process/stop/*'):
        page.locator('#stopBtn').click()
    expect(page.locator('#stopBtn')).to_be_hidden()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    upload(page)
    complete(page)


def test_parameter_change_invalidates_exports_and_expiry_is_explained(page):
    upload(page)
    complete(page)
    set_slider(page, '#kSlider', 10)
    expect(page.locator('#resultsContainer')).to_be_hidden()
    expect(page.locator('#statusMessage')).to_contain_text('Settings changed')
    complete(page)
    page.route('**/api/process/export/**', lambda route: route.fulfill(status=404,
        content_type='application/json', body=json.dumps({'detail': 'Job expired. Process the image again.'})))
    page.locator('#downloadTemplatePDF').click()
    expect(page.locator('#statusMessage')).to_contain_text('Job expired')


def test_poll_network_failure_is_bounded_and_recoverable(page):
    upload(page)
    page.route('**/api/process/status/*', lambda route: route.abort())
    page.locator('#processBtn').click()
    expect(page.locator('#colorSpace')).to_be_disabled()
    expect(page.locator('#protectEyes')).to_be_disabled()
    expect(page.locator('#statusMessage')).to_contain_text('Processing could not be completed', timeout=15000)
    expect(page.locator('#stopBtn')).to_be_hidden()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    page.unroute('**/api/process/status/*')
    complete(page)


def test_color_and_eye_controls_submit_invalidate_and_reset(page):
    expect(page.locator('#colorSpace')).to_have_value('lab')
    expect(page.locator('#protectEyes')).to_be_checked()
    upload(page)
    page.locator('#colorSpace').select_option('rgb')
    page.locator('#protectEyes').uncheck()
    complete(page)
    job_id = page.locator('#downloadTemplatePNG').get_attribute('href').split('/')[-2]
    result = page.request.get(f'/api/process/status/{job_id}').json()['result']
    assert result['parameters']['color_space'] == 'rgb'
    assert result['parameters']['protect_eyes'] is False
    assert result['eye_protection'] == {'enabled': False, 'detected_faces': 0, 'boxes': []}
    page.locator('#colorSpace').select_option('lab')
    expect(page.locator('#resultsContainer')).to_be_hidden()
    complete(page)
    page.locator('#protectEyes').check()
    expect(page.locator('#resultsContainer')).to_be_hidden()
    page.locator('#resetBtn').click()
    expect(page.locator('#colorSpace')).to_have_value('lab')
    expect(page.locator('#protectEyes')).to_be_checked()
