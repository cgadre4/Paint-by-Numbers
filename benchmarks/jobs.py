"""Diagnostic cold/style-reuse job latency and sampled process-tree RSS. No CI timing assertions."""
import argparse
import io
import json
import os
import platform
import time
from pathlib import Path

import psutil
from PIL import Image

from benchmarks.photos import MANIFEST, verified_photo
from jobs import JobManager
from settings import DEFAULTS


def wait_jobs(manager, identifiers):
    peak = 0
    owner = psutil.Process(os.getpid())
    while True:
        rss = 0
        for process in [owner] + owner.children(recursive=True):
            try:
                rss += process.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        peak = max(peak, rss)
        manager.reap()
        if all(manager.states[job]['status'] in {'Complete', 'Error', 'Stopped'} for job in identifiers):
            results = [manager.snapshot(job) for job in identifiers]
            if not all(result['status'] == 'Complete' for result in results):
                raise RuntimeError(str(results))
            return results, peak / 1024**2
        time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sides', nargs='+', type=int, default=[512, 1117, 2234])
    parser.add_argument('--colors', nargs='+', type=int, default=[8, 16, 32])
    parser.add_argument('--output', type=Path, default=Path('.local/job-benchmark'))
    args = parser.parse_args()
    if any(not 350 <= side <= 2234 for side in args.sides) or any(not 3 <= k <= 32 for k in args.colors):
        parser.error('Use sides 350-2234 and colors 3-32.')
    item = next(item for item in json.loads(MANIFEST.read_text()) if item['id'] == 'obama')
    data = verified_photo(item, Path('.local/real-photos'))
    with Image.open(io.BytesIO(data), formats=['JPEG']) as image:
        longest = max(image.size)
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(python=platform.python_version(), platform=platform.system(), logical_cpus=os.cpu_count(),
                  note='One diagnostic sample per case, including worker startup, decode and result read. Excludes server startup, HTTP and browser. RSS sums the parent and child resident sets every 50 ms; shared pages may be counted multiple times and brief peaks missed. No p95 or cross-host speed guarantee.', cases=[])
    for side in args.sides:
        for colors in args.colors:
            for concurrency in (1, 2):
                manager = JobManager()
                try:
                    params = dict(DEFAULTS, k=colors, resolution_factor=side/longest)
                    start = time.perf_counter()
                    identifiers = [manager.start(data, params) for _ in range(concurrency)]
                    results, peak = wait_jobs(manager, identifiers)
                    seconds = time.perf_counter() - start
                    start = time.perf_counter()
                    warm = manager.start(data, dict(params, stroke_width=1, canvas_width_cm=40))
                    reused, warm_peak = wait_jobs(manager, [warm])
                    warm_seconds = time.perf_counter() - start
                    assert reused[0]['result']['diagnostics']['reused_color_analysis']
                    result = results[0]['result']
                    case = dict(side=side, colors=colors, concurrency=concurrency, dimensions=result['image_dimensions'],
                                batch_seconds=round(seconds, 3), style_reuse_seconds=round(warm_seconds, 3),
                                process_tree_rss_mib=round(peak, 1), style_rss_mib=round(warm_peak, 1), stages=result['diagnostics']['stages_seconds'])
                    report['cases'].append(case)
                    print(json.dumps(case), flush=True)
                    (args.output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                finally:
                    manager.close()


if __name__ == '__main__':
    main()
