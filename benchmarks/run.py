"""Run reproducible, paired cleanup comparisons without uploading any images."""
import argparse
import hashlib
import io
import json
import platform
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from benchmarks.cases import synthetic_cases
from benchmarks.legacy import legacy_cleanup_regions
from pipeline import MAX_UPLOAD_BYTES, normalize_image, extract_contours_and_label, quantize_image
from regions import cleanup_regions, component_map

DEFAULTS = dict(k=8, resolution_factor=1, bilateral_diameter=7, sigma_color=75,
                area_min=50, stroke_width=2, font_scale=1)


def quality(labels, quantized, centers, source, placements, missing, area_min):
    _, stats, _ = component_map(labels, len(centers))
    preview = centers[labels].astype(np.uint8)
    return dict(regions=len(stats), small_regions=int((stats[:, cv2.CC_STAT_AREA] < area_min).sum()),
                unnumbered_regions=missing, numbered_regions=len(placements),
                changed_pixels_percent=round(100 * float(np.mean(labels != quantized)), 4),
                source_rgb_mae=round(float(np.abs(preview.astype(float)-source.astype(float)).mean()), 4))


def save_comparison(path, source, outputs, metrics, names=('legacy', 'adjacent')):
    h, w = source.shape[:2]
    sheet = Image.new('RGB', (w * 3, (h + 32) * 2), 'white')
    draw = ImageDraw.Draw(sheet)
    def panel(image, col, row, title):
        sheet.paste(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)), (col*w, row*(h+32)+32))
        draw.text((col*w+8, row*(h+32)+8), title, fill='black')
    panel(source, 0, 0, 'Source')
    for col, name in enumerate(names, start=1):
        panel(outputs[name][0], col, 0, name + ' preview')
        panel(outputs[name][1], col, 1, name + ' numbered template')
    for index, name in enumerate(names):
        metric = metrics[name]
        draw.text((8, h+55+index*80), f'{name}: {metric["regions"]} regions\n'
                  f'{metric["small_regions"]} small; {metric["unnumbered_regions"]} unnumbered\n'
                  f'{metric["changed_pixels_percent"]}% pixels recolored', fill='black')
    sheet.save(path)


def run_case(name, source, output, params=None, repeats=3, seed=12345):
    params = DEFAULTS.copy() if params is None else params.copy()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    timings = {stage: [] for stage in ('filter', 'quantization', 'legacy_cleanup', 'adjacent_cleanup',
                                       'legacy_render', 'adjacent_render')}
    metrics, outputs = {}, {}
    for repeat in range(repeats):
        start = time.perf_counter()
        smoothed = cv2.bilateralFilter(source, params['bilateral_diameter'], params['sigma_color'], params['sigma_color'])
        timings['filter'].append(time.perf_counter()-start)
        start = time.perf_counter()
        raw, centers = quantize_image(smoothed, params['k'], seed=seed)
        timings['quantization'].append(time.perf_counter()-start)
        # Alternate order to reduce systematic warm-cache timing bias.
        names = ('legacy', 'adjacent') if repeat % 2 == 0 else ('adjacent', 'legacy')
        for method in names:
            start = time.perf_counter()
            if method == 'legacy':
                labels = legacy_cleanup_regions(raw, params['k'], params['area_min'])
            else:
                labels = cleanup_regions(raw, params['k'], params['area_min'], centers,
                                         params['stroke_width'], params['font_scale'])
            timings[method+'_cleanup'].append(time.perf_counter()-start)
            start = time.perf_counter()
            template, placements, missing = extract_contours_and_label(labels, params['k'],
                params['stroke_width'], params['font_scale'])
            timings[method+'_render'].append(time.perf_counter()-start)
            measured = quality(labels, raw, centers, source, placements, missing, params['area_min'])
            if method in metrics and measured != metrics[method]:
                raise RuntimeError('Repeated quality metrics differ; check runtime reproducibility.')
            metrics[method] = measured
            outputs[method] = centers[labels].astype(np.uint8), template
    save_comparison(output / f'{name}.png', source, outputs, metrics)
    return dict(name=name, width=source.shape[1], height=source.shape[0],
                input_sha256=hashlib.sha256(source.tobytes()).hexdigest(), parameters=params,
                metrics=metrics, seconds_median={s: round(statistics.median(t), 6) for s, t in timings.items()},
                seconds_samples=timings, comparison=f'{name}.png')


def write_report(output, results, seed, repeats):
    report = dict(seed=seed, repeats=repeats, python=platform.python_version(), opencv=cv2.__version__,
                  numpy=np.__version__, threads=cv2.getNumThreads(), opencl=False,
                  note='Paired cleanup comparison with identical seeded K-means inputs and the current four-connected renderer. Synthetic fixtures are not a photographic quality evaluation.',
                  cases=results)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['# Region cleanup benchmark', '', report['note'], '',
             f'Seed {seed}; {repeats} repeats; {report["threads"]} CPU threads; OpenCV {cv2.__version__}.', '',
             '| Case | Regions old/new | Small old/new | Unnumbered old/new | Cleanup ms old/new | RGB MAE old/new |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for case in results:
        a, b = case['metrics']['legacy'], case['metrics']['adjacent']
        t = case['seconds_median']
        lines.append(f'| {case["name"]} | {a["regions"]}/{b["regions"]} | {a["small_regions"]}/{b["small_regions"]} | '
                     f'{a["unnumbered_regions"]}/{b["unnumbered_regions"]} | {t["legacy_cleanup"]*1000:.1f}/{t["adjacent_cleanup"]*1000:.1f} | '
                     f'{a["source_rgb_mae"]:.2f}/{b["source_rgb_mae"]:.2f} |')
    lines += ['', 'RGB MAE and recolored-pixel percentages are distortion diagnostics, not perceptual quality scores.',
              'Inspect the comparison PNGs before judging detail preservation. Timing samples include allocation and vary by machine.']
    (output / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.local/benchmarks'))
    parser.add_argument('--images', type=Path, nargs='*', help='Optional local still images; no files are uploaded.')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--colors', type=int, default=DEFAULTS['k'], choices=range(3, 33))
    parser.add_argument('--area-min', type=int, default=DEFAULTS['area_min'])
    parser.add_argument('--stroke-width', type=int, default=DEFAULTS['stroke_width'], choices=range(1, 5))
    parser.add_argument('--font-scale', type=float, default=DEFAULTS['font_scale'])
    parser.add_argument('--seed', type=int, default=12345)
    parser.add_argument('--size', type=int, default=384, help='Maximum custom image side, 32-2048 pixels.')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20 or not 32 <= args.size <= 2048 or not 0 <= args.seed < 2**31:
        parser.error('Use 1-20 repeats, size 32-2048, and seed 0 through 2147483647.')
    if not 10 <= args.area_min <= 500 or not 0.5 <= args.font_scale <= 2:
        parser.error('Use area-min 10-500 and font-scale 0.5-2.')
    settings = dict(DEFAULTS, k=args.colors, area_min=args.area_min, stroke_width=args.stroke_width, font_scale=args.font_scale)
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)
    cases = list(synthetic_cases())
    for index, path in enumerate(args.images or []):
        with path.open("rb") as stream:
            data = stream.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            parser.error("Custom images must be 20 MiB or smaller.")
        with Image.open(io.BytesIO(data)) as original:
            factor = min(1, args.size/max(original.size))
        params = dict(settings, resolution_factor=factor)
        normalized = normalize_image(data, params)
        source = cv2.imdecode(np.frombuffer(normalized, np.uint8), cv2.IMREAD_COLOR)
        if factor < 1:
            source = cv2.resize(source, (round(source.shape[1]*factor), round(source.shape[0]*factor)), interpolation=cv2.INTER_AREA)
        cases.append((f'custom_{index+1}', source))
    results = []
    for name, source in cases:
        result = run_case(name, source, args.output, params=settings, repeats=args.repeats, seed=args.seed)
        results.append(result)
        a, b = result['metrics']['legacy'], result['metrics']['adjacent']
        print(f'{name}: unnumbered {a["unnumbered_regions"]} -> {b["unnumbered_regions"]}; regions {a["regions"]} -> {b["regions"]}', flush=True)
    write_report(args.output, results, args.seed, args.repeats)
    print(f'Report: {args.output / "report.md"}', flush=True)


if __name__ == '__main__':
    main()
