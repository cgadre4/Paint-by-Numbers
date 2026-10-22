# Image and region cleanup benchmark

Run the paired benchmark from the project root:

```sh
uv run --locked python -m benchmarks.run
uv run --locked python -m benchmarks.run --colors 32 --output .local/benchmarks-32
uv run --locked python -m benchmarks.run --images path/to/photo.jpg --output .local/my-benchmark
```

On Windows, use the local environment described in the root README. All inputs stay on your computer. Outputs contain source-image previews, so keep them in ignored `.local/` and do not publish personal-image reports.

The default run creates six deterministic **synthetic** fixtures: flat shapes and a hole, a noisy landscape gradient, portrait-like details, foliage texture, speckled shapes, and thin lines with different contrast. These probe geometry and cleanup behavior; they do not replace evaluation on actual photographs. `--images` adds local still images, bounded by the application's input validation and resized to a maximum side of 384 pixels by default (`--size 32-2048`).

Default settings match the app: 8 colors, minimum region area 50 pixels, outline width 2, and requested font scale 1. Use `--colors 3-32`, `--area-min 10-500`, `--stroke-width 1-4`, and `--font-scale 0.5-2` to compare other settings. Images may still have labels reduced to the renderer's existing minimum scale of 0.3.

## Comparison method

- Each repetition filters the image and runs the app's unchanged OpenCV K-means settings with seed 12345 (override with `--seed`). The two cleanup methods receive identical labels and centers.
- `legacy.py` freezes the former wide-dilation, majority-color cleanup. It is a benchmark reference, never an application fallback.
- Both methods use the **current four-connected renderer**, which counts diagonal-only contacts separately. This isolates cleanup differences; it is not a replay of every detail of the old application.
- Run three repetitions by default (`--repeats 1-20`), alternating cleanup order. Reports include every timing sample and medians for filtering, quantization, cleanup, and rendering.
- The CLI fixes OpenCV to two CPU threads and disables OpenCL. This is a reproducible CPU comparison, not a GPU benchmark. OpenCV versions and machines may produce different results even with a fixed seed.
- Quality metrics include connected-region count, regions below the requested minimum area, unnumbered regions, pixels recolored relative to quantization, and mean absolute RGB error against the source. RGB error is a distortion diagnostic, not a perceptual quality score.

Outputs are `report.json` (settings, input hashes, runtime versions, samples, metrics), `report.md` (summary), and one comparison PNG per case. Inspect the previews and numbered templates alongside the metrics: deleting all details would reduce unnumbered regions but would be a poor result.

## Cleanup behavior

The new cleanup constructs a graph of regions sharing an actual pixel edge. It processes small regions first and selects a larger adjacent region using RGB color distance and shared boundary length. Merging a bridge also joins any newly connected regions of the target color. Region count decreases on each merge, so repeated color oscillation is impossible.

Regions below the user's minimum area are candidates regardless of contrast. Additional regions that cannot fit a complete number box qualify only when their area is at most the larger of four times the area setting or four minimum label-box areas. These extra merges require a normalized RGB distance of at most 0.08 (Euclidean RGB distance divided by the RGB cube diagonal). This conservative guard preserves stronger contrasts and large thin features, even when they remain unnumbered. Fit checks use the renderer's actual number width, stroke padding, baseline, and minimum scale, including two-digit palette IDs.

Palette IDs remain stable, and the palette can contain unused colors after cleanup. The app continues to report unnumbered regions rather than shrinking numbers indefinitely or silently deleting every difficult feature.

See [the initial measured results](RESULTS.md) for the 8- and 32-color runs and their limitations.

## Regression coverage

`tests/test_regions.py` checks edge-only adjacency, color/boundary preference, contrast and size protection, diagonal components, same-color bridge coalescing, termination on fragmented images, label containment, two-digit fit geometry, and reproducible benchmark artifacts. Performance is measured by the benchmark rather than asserted against fragile wall-clock limits in CI.
