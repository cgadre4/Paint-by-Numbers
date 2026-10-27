# Release 1.4.1 paintability review - 2026-10-27

## User-visible changes

Physical mode already calculated font and outline sizes for the chosen canvas. Auto badges, an explanation and a Pixels shortcut now make the disabled sliders understandable. A 40 cm shortcut respects the displayed centimetre/inch unit. Number ink remains at least 2 mm tall; no minimum was lowered to improve counts.

Fine retains the previous eye filter. Optional Balanced uses a 5-pixel bilateral kernel / sigma 40 instead of 3 / 25 before quantization inside protected eyes. Cleanup still preserves every resulting protected palette ID, thin contours and number exclusion. Profile changes invalidate color caches. Existing projects can regenerate with either profile and undo the complete change; format-v1 files without the new setting reopen as Fine with their stored arrays intact.

Stronger small-region cleanup explicitly selects the existing 200-pixel cutoff. Simple/Detailed defaults remain unchanged. Users should manually protect missed eyes first: stronger cleanup can remove unprotected details. The coverage summary shows numbered image area and splits missing components into those touching protected eyes and those elsewhere. Touching is a geometric classification, not proof of why a component cannot fit a number. All totals remain visible.

## Local verification

- Full suite: **193 passed in 112.12 seconds**, including 22 real-browser workflows. Added meaningful API/default/cache/profile, noisy-eye/contrast, label-diagnostic, legacy-bundle and draft/committed undo regressions.
- Eight additional still-image browser workflows at 40 cm passed. Each checked parameters, complete number-box containment, physical readability and all five PNG/PDF exports. One included manual eye marking, profile regeneration, undo and saved-profile/protected-ID validation. Desktop/mobile and portrait/landscape tiles were reviewed; the noisy portrait remains visibly busy around its eyes.
- Runtime-only installation passed processing, both profiles, manual protection, merge, undo/redo, five exports, optional original, save/reopen and clean shutdown.
- Ruff, both JavaScript syntax checks, Git whitespace and the locked dependency advisory audit passed. No new dependency or external image upload. Remote CI is checked separately for the exact published commit.
- Actual rendered PDF tiles, adjacent overlap pages, marks/ruler and PNGs were reviewed. The renderer still emits optional Symbol/ArialUnicode font-map notices; the visible text and geometry were checked. No physical printer was used. Export layout code is unchanged.

## Private 40 cm comparisons

Eight local stills supplied by the user include photographs, webcam captures and illustrations. One video was skipped because the application accepts stills. Files, names, paths, hashes, comparison images and saved projects remain local/ignored and are not published. These are functional and small-corpus observations, not a general quality study.

Both presets were evaluated at original resolution / seed 12345 and maximum side 768 / seed 42. Each image/preset used Fine, Balanced, Fine plus cutoff 200, and Balanced plus cutoff 200: **64 variants at each size/seed**. Paired cleanup choices reuse identical quantized arrays. Profile comparisons use the same resized source, mask, palette count and seed, but necessarily different upstream filtering. Detector boxes were not tightened and evaluation annotations were not supplied to production detection.

| Full-resolution aggregate, 16 image/preset cases | Missing components | Mean unnumbered area % | Mean source DE76 | Cleanup/render total seconds |
| --- | ---: | ---: | ---: | ---: |
| Fine, preset cutoff | 12,438 | 7.804 | 6.182 | 23.43 |
| Balanced, preset cutoff | 12,305 | 7.886 | 6.193 | 23.46 |
| Fine, cutoff 200 | 3,683 | 4.908 | 6.440 | 23.36 |
| Balanced, cutoff 200 | 3,548 | 5.051 | 6.450 | 23.32 |

At the second size/seed, counts were 6,006 / 5,858 / 2,096 / 1,939 and mean areas 11.689 / 11.800 / 6.730 / 6.775%, respectively. Cleanup/render totals were 6.55 / 6.51 / 6.57 / 6.45 seconds. These single local timing samples exclude decode/filter/quantization, may have concurrent host load and are not a performance guarantee.

On the grainy portrait with Detailed, Balanced reduced protected components smaller than 10 pixels from 558 to 471; total missing fell 714 to 605, but unnumbered area rose 5.027 to 5.824%. Combining cutoff 200 gave 580 missing / 5.031% area. Fine plus stronger cleanup alone gave 681 / 3.359%. The tradeoff is real; Balanced is not a universal improvement. A car Detailed case fell from 1,226 to 181 missing with cutoff 200; a webcam Detailed case fell from 688 to 108. Color distortion generally increased with stronger cleanup.

Every variant retained **zero changed protected palette IDs**. Four webcam captures had a face detected but no eye boxes; Balanced therefore had no effect there until manual marking. This release does not improve the automatic detector. A successful manual-marking browser workflow verifies the correction path, not general detector robustness.

## Public benchmark controls and optional-profile tradeoffs

Six synthetic and 18 historical RGB-photo quality dictionaries exactly matched 1.4, excluding timing fields. No frozen reference was edited. The facial comparison still runs all four RGB/Lab x protection variants; Balanced affects only the protected variants. Main facial results cover 36 cases (two humans, cat and three other images; K8/16/32, two seeds); second-size results cover six cases at 768/K16/seed2026.

The following means compare 1.4 Fine against optional Balanced with matched sources/settings/seeds. They are separate from each runner's frozen historical baseline.

| Human Lab + eye protection | Fine | Balanced |
| --- | ---: | ---: |
| Main facial missing components, mean | 283.50 | 268.25 |
| Main facial unnumbered area %, mean | 22.636 | 22.616 |
| Main facial eye DE76, mean | 9.599 | 9.740 |
| Main facial eye-edge F1 %, mean | 92.459 | 91.941 |
| Second facial missing components, mean | 361.50 | 348.50 |
| Second facial unnumbered area %, mean | 22.102 | 22.146 |
| Second facial eye-edge F1 %, mean | 90.049 | 89.687 |

Physical evaluation ran 40 pairs at 512/seed12345/20 and 40 cm, plus 40 at 768/seed42/5 and 40 cm. Within each pixel/physical pair, quantization inputs are identical. Six human portraits are reported separately from the cat:

| Human physical-mode means | Fine | Balanced |
| --- | ---: | ---: |
| Main missing components | 222.50 | 216.33 |
| Main unnumbered area % | 13.489 | 13.590 |
| Main strong-edge recall % | 83.928 | 83.903 |
| Main eye / nose / mouth F1 % | 81.617 / 58.425 / 71.418 | 81.276 / 58.398 / 71.465 |
| Second missing components | 370.08 | 362.50 |
| Second unnumbered area % | 18.462 | 18.580 |
| Second strong-edge recall % | 83.744 | 83.578 |
| Second eye / nose / mouth F1 % | 84.723 / 60.663 / 59.328 | 83.453 / 60.770 / 59.824 |

Nose/mouth means cover the four additional annotated human portraits; eye means cover all six. Cat metrics were unchanged: main facial missing mean 40 / area 10.792%, second 28 / 7.140%; main physical mean 57 / 10.630%, second 64.75 / 20.555%. The detector targets humans; unchanged cat output is not animal-eye protection. Protected-ID changes were zero throughout. Fine/Balanced crops and templates retain pupils/highlights in checked cases, but edge metrics sometimes decline. Stronger experimental filters and detector-box trimming were rejected; neither is shipped.

All edge/color metrics are proxies, and low-resolution raster contours may still crowd fine eyes. Fewer missing numbers alone is insufficient evidence of better faces. Small/angled/obscured faces, low palette counts, very small canvases and noisy sources remain limitations. Choose Simple, review the color reference and missing overlay, manually mark missed eyes and use region finishing when needed.

## Reproduction

```sh
uv run --locked python -m pytest -q
uv run --locked python -m ruff check .
uv run --locked python -m benchmarks.run --output .local/paintability-review/public-synthetic
uv run --locked python -m benchmarks.photos --output .local/paintability-review/public-photos
uv run --locked python -m benchmarks.faces --eye-detail balanced --output .local/paintability-review/public-faces
uv run --locked python -m benchmarks.faces --eye-detail balanced --size 768 --colors 16 --seeds 2026 --output .local/paintability-review/public-faces-second
uv run --locked python -m benchmarks.painting --eye-detail balanced --output .local/paintability-review/public-painting
uv run --locked python -m benchmarks.painting --eye-detail balanced --size 768 --seed 42 --widths 5 40 --output .local/paintability-review/public-painting-second
```

Cache pinned public photos first with `benchmarks.photos --download` when necessary. Offline CI never downloads photos. Omit `--eye-detail balanced` for the historical Fine behavior. Private scripts/results remain ignored; the committed synthetic/browser regressions are reproducible without private media.
