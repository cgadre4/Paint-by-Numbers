# Real-photo cleanup results - 2026-10-22

Paired against shipped commit `f8cd6b04c2ccf4127540811438a8787076626bbb`, including its original renderer. Six downloaded photographs: two portraits, two landscapes, and two detailed subjects. This is a small engineering corpus, not a representative quality study.

Primary run: maximum side 512 px without upscaling, 8/16/32 colors, seed 12345, three alternating repetitions. Python 3.12.13, OpenCV 4.14.0, Windows, two CPU threads, OpenCL disabled. Other settings: area minimum 50, outline 2, font scale 1, bilateral diameter 7 and sigma 75. Both methods receive identical cached quantization; downloads, normalization, filtering, clustering, metrics, and disk output are excluded from cleanup/render timings.

## Changes evaluated

- Equally small adjacent fragments can merge by color similarity instead of being forced toward a larger region by component ordering. This can retain detail against a contrasting background.
- Extra unnumberable-region merges allow normalized RGB distance up to 0.12 (previously 0.08), but cannot weaken an existing strong boundary to another neighbor below 0.2. The existing area cap and larger-target rule remain for extra fit merges. The explicit user area cutoff still permits stronger merges.
- Number fitting and rendering share the actual tightly cropped antialiased digit bitmap, with the same stroke padding and minimum scale 0.3. No font-size reduction was introduced.

## Primary results

Across 18 photo/palette cases, unnumbered regions fell **2,534 -> 1,716 (32.3%)**. The equal-case mean unnumbered pixel area fell **20.64% -> 15.71%**. Mean strong-edge recall rose **82.01% -> 83.61%**; mean source RGB MAE increased **10.28 -> 10.75** on a 0-255 channel scale. These means weight each case equally.

| Colors | Unnumbered before | Unnumbered after | Reduction |
| --- | ---: | ---: | ---: |
| 8 | 758 | 531 | 29.9% |
| 16 | 929 | 594 | 36.1% |
| 32 | 847 | 591 | 30.2% |

| Photo / colors | Unnumbered before/after | Unnumbered area % | Strong-edge recall % | Source RGB MAE |
| --- | ---: | ---: | ---: | ---: |
| astronaut / 8 | 212 / 214 | 20.744 / 20.749 | 88.138 / 87.983 | 13.9353 / 13.9263 |
| astronaut / 16 | 349 / 201 | 33.549 / 20.502 | 88.261 / 89.99 | 10.7767 / 12.0115 |
| astronaut / 32 | 317 / 181 | 33.109 / 21.052 | 88.421 / 89.585 | 10.0879 / 11.179 |
| obama / 8 | 198 / 173 | 27.446 / 25.2 | 91.003 / 90.835 | 13.9085 / 14.1997 |
| obama / 16 | 304 / 237 | 37.856 / 29.555 | 84.664 / 85.153 | 11.0 / 12.0295 |
| obama / 32 | 301 / 223 | 40.514 / 32.552 | 84.467 / 85.868 | 10.563 / 11.2784 |
| mountain_lake / 8 | 95 / 40 | 12.238 / 5.853 | 74.914 / 75.95 | 12.5521 / 13.0444 |
| mountain_lake / 16 | 83 / 33 | 13.108 / 8.597 | 65.313 / 69.069 | 10.538 / 10.8411 |
| mountain_lake / 32 | 57 / 41 | 14.658 / 11.345 | 69.071 / 69.94 | 10.555 / 10.6539 |
| coast / 8 | 69 / 32 | 7.452 / 3.668 | 81.67 / 81.776 | 9.4716 / 9.7876 |
| coast / 16 | 61 / 18 | 6.951 / 1.825 | 80.424 / 81.345 | 8.2201 / 8.6648 |
| coast / 32 | 65 / 32 | 11.775 / 6.227 | 85.312 / 86.016 | 7.1835 / 7.6463 |
| chelsea / 8 | 67 / 29 | 13.632 / 6.8 | 80.753 / 83.891 | 11.0457 / 11.5947 |
| chelsea / 16 | 47 / 36 | 18.495 / 16.381 | 74.818 / 83.577 | 10.8894 / 11.1813 |
| chelsea / 32 | 31 / 38 | 15.177 / 15.71 | 64.748 / 66.906 | 11.0994 / 11.279 |
| flower / 8 | 117 / 43 | 15.201 / 7.788 | 91.858 / 91.97 | 8.2152 / 8.8907 |
| flower / 16 | 85 / 69 | 25.254 / 23.735 | 92.051 / 93.147 | 7.5526 / 7.6813 |
| flower / 32 | 76 / 76 | 24.441 / 25.247 | 90.253 / 92.071 | 7.5139 / 7.6547 |

Unnumbered counts improved in 15/18 cases, were unchanged for flower/32, and regressed for astronaut/8 (212 -> 214) and cat/32 (31 -> 38). Strong-edge recall improved in 16/18, with small regressions for astronaut/8 and Obama/8. More retained detail can mean more unnumbered regions; this is not a universal improvement on every metric.

| Stage | Mean of per-case medians, before | After |
| --- | ---: | ---: |
| Cleanup | 143.96 ms | 142.92 ms |
| Rendering | 48.63 ms | 44.13 ms |

These local timings show no material average slowdown on this corpus; they are not performance guarantees.

## Seed and size sensitivity

A second run uses seed 42, maximum side 768 px, 16 colors, and three repetitions. Astronaut and cat stay at their smaller native sizes. This reuses the same six photos, so it is a sensitivity check, not independent held-out validation.

Unnumbered regions: **1,344 -> 818 (39.1%)**. Mean unnumbered area: **21.51% -> 16.10%**. Mean edge recall: **80.02% -> 81.33%**. Mean source RGB MAE: **9.39 -> 9.98**. All six cases improve missing counts and edge recall.

| Photo | Unnumbered before/after | Strong-edge recall before/after % |
| --- | ---: | ---: |
| astronaut | 361 / 233 | 88.663 / 90.34 |
| obama | 469 / 336 | 79.132 / 79.29 |
| mountain_lake | 148 / 64 | 56.369 / 58.41 |
| coast | 128 / 37 | 83.548 / 84.834 |
| chelsea | 57 / 40 | 83.791 / 85.44 |
| flower | 181 / 108 | 88.601 / 89.652 |

## Visual review and limitations

- Reviewed source, before/after preview, and numbered-template panels for all six photos at 16 colors, plus the cat/32 regression. Mountain and coast templates have fewer disconnected fragments. Portrait clothing and backgrounds simplify; difficult facial details remain. Cat contours retain more eye/fur structure, including some regions too small for numbers. The flower center still loses substantial fine detail.
- Strong-edge recall is a geometric proxy against raw K-means labels, not semantic or perceptual ground truth. It uses normalized RGB contrast >= 0.2 and one-pixel matching tolerance. It cannot detect detail already lost during resizing, filtering, or quantization. It does not establish that eyes, faces, or all important details are preserved.
- At 512 pixels, thick outlines and fine features still compete for limited space. Hundreds of portrait regions can remain unnumbered. Warnings remain truthful. Physical canvas sizing, semantic segmentation, and manual region editing are not implemented.
- Broader merging smooths color transitions: mean RGB error increases. A threshold-only trial reduced missing counts further but hurt cat edge recall, so it was rejected in favor of the strong-boundary guard.
- This corpus is public-domain/CC0 and small; test additional lighting, skin tones, objects, and larger outputs before broad quality claims.

## Verification

- `uv run --locked python -m pytest -q`: **67 passed**, including 8 real-browser workflows; one existing Starlette/httpx deprecation warning. New checks cover fragment selection, boundary protection, all 32 antialiased digits, offline downloads/checksums, metrics, and report generation.
- Reran six synthetic fixtures with three repetitions at 8 and 32 colors. Against the older wide-dilation reference using the new renderer, missing totals were 247 -> 49 and 908 -> 68. Do not compare those baselines directly with the frozen photo baseline or the historical renderer in RESULTS.md.
- Visually checked freshly rendered A4 landscape/portrait templates and the 32-color palette, plus photographic PNG comparisons. No export layout defect observed.
- These are local results. No new GitHub CI run or publication is claimed.

## Reproduce

```sh
uv run --locked python -m benchmarks.photos --download
uv run --locked python -m benchmarks.photos --colors 16 --size 768 --seed 42 --output .local/photo-sensitivity
```

Detailed reports and PNGs are generated under `.local/real-photo-benchmark/` (the recorded second run used `.local/photo-holdout/`). Source images and quantization caches are ignored. See [methodology](README.md) and [pinned source manifest](photo_sources.json).

## Sources

- [astronaut](https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut): NASA; Public domain (NASA). Original bytes and SHA-256 are pinned in the manifest.
- [obama](https://commons.wikimedia.org/wiki/File:President_Barack_Obama.jpg): Pete Souza / White House; US federal government public domain. Original bytes and SHA-256 are pinned in the manifest.
- [mountain_lake](https://commons.wikimedia.org/wiki/File:Lake_in_a_mountain_valley_(Unsplash).jpg): Veri Ivanova; CC0-1.0. Original bytes and SHA-256 are pinned in the manifest.
- [coast](https://commons.wikimedia.org/wiki/File:The_Coast_(Unsplash).jpg): Jakob Owens; CC0-1.0. Original bytes and SHA-256 are pinned in the manifest.
- [chelsea](https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.chelsea): Stefan van der Walt; CC0-1.0. Original bytes and SHA-256 are pinned in the manifest.
- [flower](https://commons.wikimedia.org/wiki/File:Flower_petals_macro.jpg): Jon Sullivan; Public domain dedication. Original bytes and SHA-256 are pinned in the manifest.
