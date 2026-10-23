"""Paired real-photo evaluation against the exact f8cd6b0 implementation."""
import argparse
import hashlib
import io
import json
import platform
import statistics
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from benchmarks import adjacent_v1
from benchmarks.run import DEFAULTS, quality, save_comparison
from pipeline import MAX_UPLOAD_BYTES, normalize_image, quantize_image, extract_contours_and_label
from regions import cleanup_regions

MANIFEST = Path(__file__).with_name('photo_sources.json')


def verified_photo(item, directory, download=False):
    name = item['filename']
    if not name or name in ('.', '..') or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('Photo filename must be a basename.')
    path = Path(directory) / name
    if not path.exists() and download:
        if not item['download_url'].startswith('https://'):
            raise ValueError('Photo downloads require HTTPS.')
        request = urllib.request.Request(item['download_url'], headers={'User-Agent': 'PaintByNumbersBenchmark/1.0'})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(MAX_UPLOAD_BYTES+1)
        if len(data) > MAX_UPLOAD_BYTES or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError(f'Download size or SHA-256 mismatch: {name}')
        # Decode/validate before publishing the downloaded cache entry.
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if not path.exists():
        raise FileNotFoundError(f'{path} is missing; rerun with --download.')
    with path.open('rb') as stream:
        data = stream.read(MAX_UPLOAD_BYTES+1)
    if len(data) > MAX_UPLOAD_BYTES or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError(f'Photo SHA-256 mismatch: {name}')
    return data


def load_photo(data, size, k):
    with Image.open(io.BytesIO(data)) as image:
        factor = min(1, size/max(image.size))
    normalized = normalize_image(data, dict(DEFAULTS, k=k, resolution_factor=factor))
    source = cv2.imdecode(np.frombuffer(normalized, np.uint8), cv2.IMREAD_COLOR)
    if factor < 1:
        source = cv2.resize(source, (round(source.shape[1]*factor), round(source.shape[0]*factor)), interpolation=cv2.INTER_AREA)
    return source


def strong_edges(labels, centers, threshold=0.2):
    preview = centers[labels].astype(np.float32)
    mask = np.zeros(labels.shape, np.uint8)
    for axis in (0, 1):
        contrast = np.linalg.norm(np.diff(preview, axis=axis), axis=2) / (255*np.sqrt(3))
        if axis == 0:
            mask[:-1] |= contrast >= threshold
            mask[1:] |= contrast >= threshold
        else:
            mask[:, :-1] |= contrast >= threshold
            mask[:, 1:] |= contrast >= threshold
    return mask


def detail_metrics(labels, raw, centers, placements):
    reference = strong_edges(raw, centers)
    retained = cv2.dilate(strong_edges(labels, centers), np.ones((3, 3), np.uint8))
    edge_count = np.count_nonzero(reference)
    recall = float(np.count_nonzero(reference & retained) / edge_count) if edge_count else 1.0
    # Numbered pixel coverage measures the size, not only the count, of missing regions.
    from regions import component_map
    components, stats, _ = component_map(labels, len(centers))
    numbered = set()
    for p in placements:
        x, y, w, h = p['box']
        numbered.add(int(components[y+h//2, x+w//2]))
    covered = sum(int(stats[node, cv2.CC_STAT_AREA]) for node in numbered)
    return dict(strong_edge_recall_percent=round(100*recall, 3),
                unnumbered_area_percent=round(100*(1-covered/labels.size), 3))


def prepare_quantization(source, params, seed, cache):
    signature = hashlib.sha256(source.tobytes()+str(source.shape).encode()+json.dumps(params, sort_keys=True).encode()+str(seed).encode()+cv2.__version__.encode()).hexdigest()
    path = Path(cache)/f'{signature}.npz'
    if path.exists():
        with np.load(path, allow_pickle=False) as data:
            return data['labels'], data['centers']
    filtered = cv2.bilateralFilter(source, params['bilateral_diameter'], params['sigma_color'], params['sigma_color'])
    raw, centers = quantize_image(filtered, params['k'], seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, labels=raw, centers=centers)
    return raw, centers


def compare_photo(item, source, params, output, cache, repeats=3, seed=12345):
    raw, centers = prepare_quantization(source, params, seed, cache)
    methods = dict(baseline=(adjacent_v1.cleanup_regions, adjacent_v1.extract_contours_and_label),
                   current=(cleanup_regions, extract_contours_and_label))
    metrics, outputs = {}, {}
    timing = {name: [] for name in methods}
    render_time = {name: [] for name in methods}
    for repeat in range(repeats):
        for name in (list(methods) if repeat%2 == 0 else list(reversed(methods))):
            clean, render = methods[name]
            start = time.perf_counter()
            labels = clean(raw, params['k'], params['area_min'], centers, params['stroke_width'], params['font_scale'])
            timing[name].append(time.perf_counter()-start)
            start = time.perf_counter()
            template, placements, missing = render(labels, params['k'], params['stroke_width'], params['font_scale'])
            render_time[name].append(time.perf_counter()-start)
            measured = quality(labels, raw, centers, source, placements, missing, params['area_min'])
            measured.update(detail_metrics(labels, raw, centers, placements))
            if name in metrics and metrics[name] != measured:
                raise RuntimeError('Repeated photo metrics differ.')
            metrics[name] = measured
            outputs[name] = centers[labels].astype(np.uint8), template
    ident = f'{item["id"]}-{params["k"]}'
    save_comparison(Path(output)/f'{ident}.png', source, outputs, metrics, names=('baseline', 'current'))
    return dict(id=item['id'], category=item['category'], source=item, parameters=params, seed=seed,
                width=source.shape[1], height=source.shape[0], metrics=metrics,
                cleanup_seconds={name: statistics.median(t) for name, t in timing.items()},
                render_seconds={name: statistics.median(t) for name, t in render_time.items()},
                cleanup_samples=timing, render_samples=render_time, comparison=f'{ident}.png')


def write_report(output, report):
    output = Path(output)
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# Real-photo cleanup benchmark', '',
             f"Baseline: `{report['baseline_commit']}`. Seed {report['seed']}; maximum side {report['size']} px; {report['repeats']} alternating repetitions.",
             'Identical cached K-means inputs; CPU OpenCV, two threads. Timings exclude download, preprocessing, quantization, metrics, and file output.', '',
             '| Photo / colors | Unnumbered regions | Unnumbered area % | Strong-edge recall % | RGB MAE | Cleanup ms | Render ms |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for case in report['cases']:
        a, b = case['metrics']['baseline'], case['metrics']['current']
        fields = [f"[{case['id']} / {case['parameters']['k']}]({case['comparison']})"]
        for key in ('unnumbered_regions', 'unnumbered_area_percent', 'strong_edge_recall_percent', 'source_rgb_mae'):
            fields.append(f'{a[key]} -> {b[key]}')
        for key in ('cleanup_seconds', 'render_seconds'):
            fields.append(f"{1000*case[key]['baseline']:.1f} -> {1000*case[key]['current']:.1f}")
        lines.append('| ' + ' | '.join(fields) + ' |')
    lines += ['', 'Strong-edge recall is a geometric proxy relative to raw quantization: normalized RGB distance >= 0.2, with a one-pixel matching tolerance. It is not a semantic detail score. Higher recall can preserve unnumberable details; lower region counts can erase detail.', '', '## Photo credits', '']
    sources = {case['source']['id']: case['source'] for case in report['cases']}
    for item in sources.values():
        lines.append(f"- [{item['id']}]({item['source_url']}): {item['author']}; {item['license']}. SHA-256: `{item['sha256']}`.")
    (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Download missing pinned photos from their documented sources.')
    parser.add_argument('--photos', type=Path, default=Path('.local/real-photos'))
    parser.add_argument('--output', type=Path, default=Path('.local/real-photo-benchmark'))
    parser.add_argument('--cache', type=Path, default=Path('.local/photo-quantization'))
    parser.add_argument('--size', type=int, default=512)
    parser.add_argument('--colors', type=int, nargs='+', default=[8, 16, 32])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=12345)
    args = parser.parse_args()
    if not 32 <= args.size <= 2000 or not 1 <= args.repeats <= 20 or any(not 3 <= k <= 32 for k in args.colors) or not 0 <= args.seed < 2**31:
        parser.error('Use size 32-2000, repeats 1-20, colors 3-32, and a nonnegative signed 32-bit seed.')
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    for item in json.loads(MANIFEST.read_text(encoding='utf-8')):
        data = verified_photo(item, args.photos, args.download)
        for k in args.colors:
            source = load_photo(data, args.size, k)
            case = compare_photo(item, source, dict(DEFAULTS, k=k), args.output, args.cache, args.repeats, args.seed)
            cases.append(case)
            a, b = case['metrics']['baseline'], case['metrics']['current']
            print(f'{item["id"]} k={k}: missing {a["unnumbered_regions"]}->{b["unnumbered_regions"]}; edge recall {a["strong_edge_recall_percent"]}->{b["strong_edge_recall_percent"]}; RGB MAE {a["source_rgb_mae"]}->{b["source_rgb_mae"]}', flush=True)
        report = dict(baseline_commit='f8cd6b04c2ccf4127540811438a8787076626bbb', size=args.size,
                      seed=args.seed, repeats=args.repeats, opencv=cv2.__version__, python=platform.python_version(), cases=cases)
        write_report(args.output, report)


if __name__ == '__main__':
    main()
