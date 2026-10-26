"""Offline checks: CI never downloads the photographic corpus."""
import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from benchmarks import photos
from benchmarks.run import DEFAULTS


def photo_bytes():
    stream = io.BytesIO()
    Image.new('RGB', (80, 40), (100, 120, 160)).save(stream, format='PNG')
    return stream.getvalue()


def photo_item(data):
    return dict(id='fixture', filename='fixture.png', category='detail', author='Synthetic test',
                license='Test fixture', source_url='https://example.invalid/source',
                download_url='https://example.invalid/photo.png', sha256=hashlib.sha256(data).hexdigest())


def test_download_is_opt_in_pinned_and_reused(tmp_path, monkeypatch):
    data = photo_bytes()
    item = photo_item(data)
    calls = []
    def download(request, timeout):
        calls.append(request.full_url)
        assert timeout == 45
        return io.BytesIO(data)
    monkeypatch.setattr(photos.urllib.request, 'urlopen', download)
    with pytest.raises(FileNotFoundError):
        photos.verified_photo(item, tmp_path)
    assert not calls
    assert photos.verified_photo(item, tmp_path, download=True) == data
    assert photos.verified_photo(item, tmp_path, download=True) == data
    assert calls == [item['download_url']]
    (tmp_path/item['filename']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA-256'):
        photos.verified_photo(item, tmp_path, download=True)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['checksum', 'oversize', 'decode', 'insecure'])
def test_bad_download_never_becomes_cached_file(tmp_path, monkeypatch, failure):
    data = photo_bytes()
    item = photo_item(data)
    if failure == 'checksum':
        item['sha256'] = '0'*64
    elif failure == 'oversize':
        monkeypatch.setattr(photos, 'MAX_UPLOAD_BYTES', len(data)-1)
    elif failure == 'decode':
        data = b'not an image'
        item = photo_item(data)
    else:
        item['download_url'] = 'http://example.invalid/photo.png'
    monkeypatch.setattr(photos.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(data))
    with pytest.raises((ValueError, OSError)):
        photos.verified_photo(item, tmp_path, download=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', ['../escape.png', 'nested/file.png', 'nested\\file.png', '.', '..', ''])
def test_photo_cache_rejects_paths(tmp_path, name):
    item = photo_item(photo_bytes())
    item['filename'] = name
    with pytest.raises(ValueError, match='basename'):
        photos.verified_photo(item, tmp_path, download=True)


def test_photo_metrics_and_runner_are_repeatable_offline(tmp_path):
    data = photo_bytes()
    source = photos.load_photo(data, 40, 3)
    assert source.shape == (20, 40, 3)
    assert photos.load_photo(data, 200, 3).shape == (40, 80, 3)
    source[:, 20:] = (20, 40, 60)
    source[8:12, 10:30] = (220, 230, 240)
    params = dict(DEFAULTS, k=3, area_min=10)
    first = photos.compare_photo(photo_item(data), source, params, tmp_path, tmp_path/'cache', repeats=2, seed=42)
    second = photos.compare_photo(photo_item(data), source, params, tmp_path, tmp_path/'cache', repeats=1, seed=42)
    assert first['metrics'] == second['metrics']
    report = dict(baseline_commit='f8cd6b0', seed=42, size=40, repeats=2, cases=[first])
    photos.write_report(tmp_path, report)
    assert json.loads((tmp_path/'report.json').read_text(encoding='utf-8')) == report
    assert (tmp_path/'fixture-3.png').is_file()
    assert 'Strong-edge recall' in (tmp_path/'report.md').read_text(encoding='utf-8')
    for samples in first['cleanup_samples'].values():
        assert len(samples) == 2 and min(samples) >= 0
    labels = np.zeros((40, 80), np.int32)
    labels[:, 40:] = 1
    centers = np.array([[0]*3, [255]*3], np.float32)
    placements = [{'id': 1, 'box': [10, 10, 10, 10]}]
    metrics = photos.detail_metrics(labels, labels, centers, placements)
    assert metrics == dict(strong_edge_recall_percent=100.0, unnumbered_area_percent=50.0)
    assert photos.detail_metrics(labels*0, labels, centers, [])['strong_edge_recall_percent'] == 0
    assert photos.detail_metrics(labels*0, labels*0, centers, [])['strong_edge_recall_percent'] == 100
