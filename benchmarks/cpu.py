"""Evaluate sampled CPU palette fitting. This experiment is not an app mode."""
import argparse
import json
import platform
import random
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

from benchmarks.faces import ANNOTATIONS, annotation_masks, color_metrics, comparison
from benchmarks.photos import MANIFEST, verified_photo, load_photo, detail_metrics
from benchmarks.run import quality
from facial import detect_eyes, eye_mask, preserve_eye_pixels
from pipeline import quantize_image, extract_contours_and_label
from regions import cleanup_regions


def sampled_quantization(image, k, seed=12345, sample_limit=65536):
    """Fit a uniform seeded sample; assign every pixel in bounded native chunks."""
    if image.shape[0] * image.shape[1] <= sample_limit:
        return quantize_image(image, k, seed, 'lab')
    pixels = cv2.cvtColor(image.astype(np.float32) / 255, cv2.COLOR_BGR2Lab).reshape(-1, 3)
    indices = random.Random(seed).sample(range(len(pixels)), sample_limit)
    cv2.setRNGSeed(seed)
    _, _, centers = cv2.kmeans(pixels[indices], k, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 100 / 255), 10, cv2.KMEANS_RANDOM_CENTERS)
    labels = np.empty(len(pixels), np.int32)
    for start in range(0, len(pixels), 65536):
        _, nearest = cv2.batchDistance(pixels[start:start+65536], centers, cv2.CV_32F, normType=cv2.NORM_L2SQR, K=1)
        labels[start:start+65536] = nearest[:, 0]
    colors = np.clip(cv2.cvtColor(centers[None], cv2.COLOR_Lab2BGR)[0] * 255, 0, 255)
    return labels.reshape(image.shape[:2]), colors


def run_case(item, source, k, seed, output, repeats):
    annotations = json.loads(ANNOTATIONS.read_text(encoding='utf-8-sig'))['images']
    eyes, face, face_box = annotation_masks(source.shape, annotations.get(item['id']))
    boxes, _ = detect_eyes(source)
    protected = eye_mask(source.shape, boxes)
    smoothed = cv2.bilateralFilter(source, 7, 75, 75)
    smoothed = preserve_eye_pixels(source, smoothed, protected)
    metrics, outputs, durations = {}, {}, {'full': [], 'sampled': []}
    methods = {'full': lambda: quantize_image(smoothed, k, seed, 'lab'), 'sampled': lambda: sampled_quantization(smoothed, k, seed)}
    for repeat in range(repeats):
        for name in (list(methods) if repeat % 2 == 0 else list(reversed(methods))):
            start = time.perf_counter()
            raw, centers = methods[name]()
            durations[name].append(time.perf_counter() - start)
            if repeat:
                continue
            labels = cleanup_regions(raw, k, 50, centers, 2, 1, protected)
            template, placements, missing = extract_contours_and_label(labels, k, 2, 1, protected)
            preview = centers[labels].astype(np.uint8)
            metrics[name] = quality(labels, raw, centers, source, placements, missing, 50)
            metrics[name].update(color_metrics(source, preview, eyes, face))
            metrics[name].update(detail_metrics(labels, raw, centers, placements))
            metrics[name]['protected_pixels_changed'] = int(np.count_nonzero(labels[protected] != raw[protected]))
            outputs[name] = dict(preview=preview, raw=centers[raw].astype(np.uint8), template=template)
    ident = f"{item['id']}-{source.shape[1]}x{source.shape[0]}-{k}-{seed}"
    comparison(output / (ident + '.png'), source, outputs, face_box)
    return dict(id=ident, metrics=metrics, quantization_seconds={name: statistics.median(values) for name, values in durations.items()}, samples_seconds=durations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', type=int, default=512)
    parser.add_argument('--colors', type=int, nargs='+', default=[8, 16, 32])
    parser.add_argument('--seed', type=int, default=12345)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, default=Path('.local/cpu-benchmark'))
    args = parser.parse_args()
    if not 32 <= args.size <= 2000 or not 1 <= args.repeats <= 10 or any(not 3 <= k <= 32 for k in args.colors):
        parser.error('Use size 32-2000, 1-10 repeats and 3-32 colors.')
    cv2.setNumThreads(2)
    cv2.ocl.setUseOpenCL(False)
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(python=platform.python_version(), opencv=cv2.__version__, threads=2, size=args.size, seed=args.seed, cases=[])
    for item in json.loads(MANIFEST.read_text()):
        data = verified_photo(item, Path('.local/real-photos'))
        for k in args.colors:
            source = load_photo(data, args.size, k)
            case = run_case(item, source, k, args.seed, args.output, args.repeats)
            report['cases'].append(case)
            print(case['id'], case['quantization_seconds'], flush=True)
            (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
