# Image and region cleanup benchmark

Run the paired benchmark from the project root:

```sh
uv run --locked python -m benchmarks.run
uv run --locked python -m benchmarks.run --colors 32 --output .local/benchmarks-32
uv run --locked python -m benchmarks.run --images path/to/photo.jpg --output .local/my-benchmark
```

On Windows, use the local environment described in the root README. All inputs stay on your computer. Outputs contain source-image previews, so keep them in ignored `.local/` and do not publish personal-image reports.

The default run creates six deterministic **synthetic** fixtures: flat shapes and a hole, a noisy landscape gradient, portrait-like details, foliage texture, speckled shapes, and thin lines with different contrast. These probe geometry and cleanup behavior; they do not replace evaluation on actual photographs. `--images` adds local still images, bounded by the application's input validation and resized to a maximum side of 384 pixels by default (`--size 32-2048`).

Numeric defaults match the app; these historical cleanup comparisons deliberately retain RGB quantization with eye protection off: 8 colors, minimum region area 50 pixels, outline width 2, and requested font scale 1. Use `--colors 3-32`, `--area-min 10-500`, `--stroke-width 1-4`, and `--font-scale 0.5-2` to compare other settings. Images may still have labels reduced to the renderer's existing minimum scale of 0.3.

## Comparison method

- Each repetition filters the image and runs the historical RGB OpenCV K-means settings with seed 12345 (override with `--seed`). The two cleanup methods receive identical labels and centers.
- `legacy.py` freezes the former wide-dilation, majority-color cleanup. It is a benchmark reference, never an application fallback.
- Both methods use the **current four-connected renderer**, which counts diagonal-only contacts separately. This isolates cleanup differences; it is not a replay of every detail of the old application.
- Run three repetitions by default (`--repeats 1-20`), alternating cleanup order. Reports include every timing sample and medians for filtering, quantization, cleanup, and rendering.
- The CLI fixes OpenCV to two CPU threads and disables OpenCL. This is a reproducible CPU comparison, not a GPU benchmark. OpenCV versions and machines may produce different results even with a fixed seed.
- Quality metrics include connected-region count, regions below the requested minimum area, unnumbered regions, pixels recolored relative to quantization, and mean absolute RGB error against the source. RGB error is a distortion diagnostic, not a perceptual quality score.

Outputs are `report.json` (settings, input hashes, runtime versions, samples, metrics), `report.md` (summary), and one comparison PNG per case. Inspect the previews and numbered templates alongside the metrics: deleting all details would reduce unnumbered regions but would be a poor result.

## Cleanup behavior

The new cleanup constructs a graph of regions sharing an actual pixel edge. It processes small regions first and selects an adjacent region using RGB color distance and shared boundary length. Merging a bridge also joins any newly connected regions of the target color. Region count decreases on each merge, so repeated color oscillation is impossible.

Regions below the user's minimum area are candidates regardless of contrast, unless protected by an eye mask. The historical runners supply no mask. Additional regions that cannot fit a complete number box qualify only when their area is at most the larger of four times the area setting or four minimum label-box areas. These extra merges require a normalized RGB distance of at most 0.12 (Euclidean RGB distance divided by the RGB cube diagonal). Extra merges must also preserve every existing strong boundary (normalized distance at least 0.2) to other neighbors, and stay anchored to an at-least-as-large region. Below the explicit area cutoff, equally small neighbors compete by color and shared boundary rather than component ID. This avoids needlessly erasing similarly colored fragments against a contrasting background. Fit checks use cached, tightly cropped antialiased digit bitmaps with unchanged stroke padding and minimum font scale. Cleanup and rendering use exactly the same padded box, including two-digit palette IDs.

Palette IDs remain stable, and the palette can contain unused colors after cleanup. The app continues to report unnumbered regions rather than shrinking numbers indefinitely or silently deleting every difficult feature.

See [the initial measured results](RESULTS.md) for the 8- and 32-color runs and their limitations.

## Regression coverage

`tests/test_regions.py` checks edge-only adjacency, color/boundary preference, contrast and size protection, diagonal components, same-color bridge coalescing, termination on fragmented images, label containment, two-digit fit geometry, and reproducible benchmark artifacts. Performance is measured by the benchmark rather than asserted against fragile wall-clock limits in CI.

## Real photographs

```sh
uv run --locked python -m benchmarks.photos --download
uv run --locked python -m benchmarks.photos --colors 16 --size 768 --seed 42 --output .local/photo-sensitivity
```

The first command explicitly downloads six public-domain/CC0 images: two portraits, a mountain lake, a coast, cat fur, and a macro flower. `photo_sources.json` records authors, license descriptions, original source pages, download URLs, and SHA-256 digests. Existing files are verified and reused; changed upstream bytes cause an error rather than silently changing the corpus. Without `--download`, missing photos cause an error. Photos stay under ignored `.local/real-photos/`; nothing is uploaded. Offline tests mock downloads and do not need internet access.

The photo runner compares **both cleanup and rendering** against `adjacent_v1.py`, a frozen copy of the shipped `f8cd6b0` implementation. This is a different baseline from the synthetic runner's older `legacy.py`. Never import either reference implementation into the app.

Defaults: maximum image side 512 pixels without upscaling, palettes 8/16/32, seed 12345, three alternating repetitions, and the other app defaults. `--photos`, `--output`, and `--cache` select local directories. Normalization, source limits, and bilateral filtering match the app. Quantization remains historical RGB with eye protection off; use the facial runner for the current default. Quantization is cached by source pixels/shape, parameters, seed, and OpenCV version; both methods receive identical arrays. The reported cleanup/render timings exclude downloading, preprocessing, quantization, metrics, and disk output. Timing varies with hardware and machine load.

In addition to the synthetic metrics, reports show the percentage of pixels in unnumbered regions and strong-edge recall relative to raw quantization. A strong edge has normalized RGB contrast >= 0.2; matching tolerates one pixel in either axis. An input without strong edges scores 100%. This geometric proxy does not recognize faces or establish semantic detail preservation. Higher recall can retain regions too small to number; RGB MAE and visual inspection remain necessary. Means in the results summary weight photo/settings cases equally, not by pixel count.

See [real-photo measurements and limitations](PHOTO_RESULTS.md). JSON, Markdown, and source/baseline/current comparison PNGs are generated under `.local/real-photo-benchmark/`. The downloaded originals and generated images are deliberately excluded from Git.

## Perceptual colors and facial detail

```sh
uv run --locked python -m benchmarks.faces
uv run --locked python -m benchmarks.faces --colors 16 --seeds 2026 --size 768 --output .local/facial-size-check
```

Cache the pinned photos with `benchmarks.photos --download` first. The facial runner never downloads images. It compares four variants: RGB, float CIELab, RGB with eye protection, and Lab with eye protection. The unprotected variants use the frozen cleanup/renderer from `6b657ba` in `facial_baseline.py`; RGB quantization is frozen there too. Both spaces use the same palette budget, ten K-means restarts and ten-iteration cap; Lab's convergence tolerance is scaled to its luminance range. Protected variants additionally use mild bilateral filtering inside detected eye boxes, preserve intersecting connected components during cleanup, use thin eye outlines, and exclude numbers from those boxes.

Default settings are palettes 8/16/32, seeds 12345/42, maximum side 512 without upscaling, two CPU threads, and OpenCL disabled. The size/seed check uses the same images, not a held-out corpus. `face_annotations.json` contains manually checked eye/face evaluation boxes for the two humans and cat, scaled with the input. These annotations never guide detection or processing.

All methods are compared with the same resized original: mean Euclidean float-Lab distance (Delta E 76), plus source Canny edges (thresholds 60/120, one-pixel tolerance) inside fixed eye/face boxes. Edge F1 balances precision and recall but is not semantic or perceptual ground truth. Report human eyes separately from the cat, which this detector does not target. Also compare missing-region counts, unnumbered pixel coverage, raw/pre-cleanup metrics, and visual previews/templates. Diagnostic single-sample quantization/cleanup timings and detector time are recorded separately; these are not a controlled speed comparison.

Outputs under ignored `.local/facial-benchmark/` include JSON metrics/settings/detected boxes, a Markdown table, and comparison PNGs with full previews, raw cluster crops, cleaned crops, and template crops. Source images, downloads, and generated previews remain excluded from Git. The offline test suite checks runner artifacts using synthetic inputs. See [the measured decision and limitations](FACIAL_RESULTS.md).
