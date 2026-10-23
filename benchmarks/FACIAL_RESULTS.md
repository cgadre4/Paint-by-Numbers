# Facial-detail comparison: RGB versus perceptual Lab

Measured on 2026-10-22 with Python 3.12.13 / OpenCV 4.14.0, two CPU threads and OpenCL off. Photo sources, credits, licenses and checksums are in [photo_sources.json](photo_sources.json). The baseline is the shipped `6b657ba2cc783edc22cd084126347b52fd274e7c` implementation.

## Decision

Use **Lab K-means plus local human-eye protection** by default. Keep Classic RGB and the eye-protection toggle available. Lab improves color fidelity on this corpus; eye protection prevents cleanup from deleting pupils and highlights. Lab alone does not solve eye loss. This changes the color-distance objective of OpenCV K-means, not the clustering algorithm to a neural model.

The six photos were each processed at 8, 16 and 32 colors with seeds 12345 and 42: 36 cases, 144 outputs. Maximum side 512 pixels, no upscaling; area minimum 50, stroke 2, font scale 1, bilateral diameter 7 and sigma 75. Both spaces use ten random restarts and ten iterations, with convergence tolerance scaled to Lab luminance units. Protected variants use a mild diameter-3/sigma-25 filter inside detected eyes. Unprotected cleanup/rendering and RGB quantization are frozen in `facial_baseline.py`.

All color errors compare against the same resized original using Euclidean float CIELab distance (Delta E 76). Eye-edge F1 compares Canny 60/120 edges with one-pixel tolerance inside manually checked, fixed evaluation boxes. These proxies are not semantic ground truth. The annotations never influence the detector. Means weight cases equally; human-eye means cover 12 cases from two people, excluding the cat.

## Main results

| Method | Source DE76, lower better | Human eye DE76 | Human eye F1 % | Unnumbered total | Mean unnumbered area % |
| --- | ---: | ---: | ---: | ---: | ---: |
| rgb | 7.325 | 16.137 | 75.625 | 3,517 | 16.110 |
| lab | 6.862 | 12.611 | 77.913 | 3,290 | 14.057 |
| rgb_eyes | 7.318 | 12.134 | 94.949 | 5,357 | 16.182 |
| lab_eyes | 6.859 | 9.599 | 92.459 | 4,360 | 13.993 |

Relative to the old RGB pipeline, the selected default reduces mean source color error by 6.4% and human-eye color error by 40.5%, while eye-edge F1 rises 16.8 percentage points. Mean whole-face color error falls from 12.254 to 9.795; whole-face edge F1 rises from 65.563% to 67.785%. RGB with protection achieves higher eye F1 (94.949%) but worse eye color error and more unnumbered regions than Lab with protection. The decision balances these measurements with visual inspection of the face crops.

Preserving fine eye regions raises the total unnumbered count by 24.0% versus old RGB, even though the mean unnumbered pixel area is lower. More small regions are an intentional cost of retaining eye structure. Numbers are excluded from eye boxes, and outlines there are one pixel wide. Very small template features can still be crowded or too dark to paint from the outline alone; use the color preview as reference. Increasing the palette is not a guarantee of easier painting.

| Portrait | K | Seed | Old eye DE76 | New eye DE76 | Old eye F1 % | New eye F1 % | Old missing | New missing |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| astronaut | 8 | 12345 | 15.730 | 12.740 | 83.408 | 81.734 | 214 | 202 |
| astronaut | 8 | 42 | 15.664 | 12.714 | 80.945 | 92.873 | 228 | 227 |
| astronaut | 16 | 12345 | 14.642 | 7.787 | 83.243 | 93.190 | 201 | 324 |
| astronaut | 16 | 42 | 14.806 | 7.893 | 81.557 | 92.880 | 233 | 272 |
| astronaut | 32 | 12345 | 12.035 | 5.821 | 64.663 | 95.469 | 181 | 360 |
| astronaut | 32 | 42 | 12.268 | 7.423 | 68.818 | 93.127 | 218 | 320 |
| obama | 8 | 12345 | 21.687 | 14.307 | 66.182 | 92.308 | 173 | 174 |
| obama | 8 | 42 | 21.600 | 13.371 | 77.047 | 90.088 | 207 | 168 |
| obama | 16 | 12345 | 19.272 | 8.738 | 66.150 | 93.335 | 237 | 294 |
| obama | 16 | 42 | 16.634 | 9.534 | 72.567 | 89.278 | 234 | 275 |
| obama | 32 | 12345 | 16.649 | 7.128 | 76.630 | 97.385 | 223 | 383 |
| obama | 32 | 42 | 12.654 | 7.735 | 86.290 | 97.835 | 181 | 403 |

The 8-color astronaut at seed 12345 is a regression in eye-edge F1 (83.408% to 81.734%) despite better color error. Both detected eyes retain their quantized pixels, but the palette can already have lost detail. Start with 16-32 colors for portraits and review the result. Keeping RGB selectable allows comparison when its edges look better.

The detector found both eyes in each human portrait and no eyes in the four other photos at 512 pixels. Protected cleanup changed **zero** palette IDs inside the detected eye masks in every protected case. This is a cleanup invariant, not a guarantee that quantization or detection preserves every original feature. The cat is outside the human detector scope; its results are included in global color/label measurements, not human-eye statistics.

## Size and seed check

Six additional cases used seed 2026, 16 colors and maximum side 768, again without upscaling. Astronaut remains 512x512 and cat remains 451x300; Obama becomes 615x768. These are the same photos, not independent validation. Mean source DE76 fell from 6.715 to 6.422; human-eye DE76 from 17.023 to 10.057; human-eye F1 rose from 76.216% to 90.049%. Unnumbered total rose from 921 to 990. Both human portraits again had two detected eyes and zero protected pixels recolored by cleanup.

## Verification and limits

- All 87 local tests passed, including 9 real-browser workflows. New regressions cover Lab conversion and seeded results, protected components/coalescing, detector scaling/model availability, thin outlines and text exclusion, API option validation/defaults, worker metadata, and offline benchmark outputs.
- Real-browser checks uploaded both portraits, processed with the new default, confirmed two detected eyes, and downloaded all four PNG/PDF exports. Desktop/mobile layout, 8/16/32-color face comparisons, portrait and landscape templates, and the full 32-color palette PDF were inspected.
- Reran the synthetic and historical RGB photo benchmarks. These keep their old color settings for comparability; they do not measure the new default.
- Detection uses bundled OpenCV frontal-face/eye cascades, entirely locally. Tiny, angled, obscured, or unusual faces may be missed; false positives are possible. Only two human subjects were evaluated, so demographic/generalization quality is not established. A detection notice explains whether protection ran.
- No new dependency, image upload, GPU backend, face recognition, manual editing, or vector output was added.
- Detector calls measured 0.040-0.085 seconds in the main run and up to 0.122 seconds in the size check on this machine. Quantization/cleanup samples are recorded per case but were not isolated from other machine activity; no speed improvement is claimed.

## Reproduce

```sh
uv run --locked python -m benchmarks.photos --download
uv run --locked python -m benchmarks.faces --output .local/facial-benchmark-final
uv run --locked python -m benchmarks.faces --colors 16 --seeds 2026 --size 768 --output .local/facial-size-check
```

Reports contain per-case metrics, raw/pre-cleanup comparisons, runtime versions, detected boxes, timing samples and full/cropped comparison PNGs. Generated reports and photo binaries stay ignored under `.local/`; source URLs and hashes remain tracked.

Implementation references: [OpenCV color conversions](https://docs.opencv.org/4.13.0/de/d25/imgproc_color_conversions.html) describes normalized float input and Lab conversion; [OpenCV cascade classifier tutorial](https://docs.opencv.org/4.13.0/db/d28/tutorial_cascade_classifier.html) describes the local face/eye detection approach. Runtime version for these measurements is recorded above.
