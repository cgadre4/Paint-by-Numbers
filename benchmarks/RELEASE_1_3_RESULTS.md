# Release 1.3 validation — 2026-10-26

## Verification

Final local suite from the nonsynced project copy: **140 passed in 32.75 s**, including 12 real-browser workflows. Three additional real-photo browser workflows passed in 24.62 s. Ruff, JavaScript syntax and Git whitespace checks passed. The locked runtime/dev advisory scan reported no known vulnerabilities. A fresh runtime-only 1.3 environment also passed server startup, upload, processing and all five exports, then shut down cleanly. Remote CI is verified separately for the exact published commit; these are local results.

Visually reviewed actual portrait/landscape tiled PDFs, adjacent overlap pages, crop/alignment marks, 50 mm rulers, the complete 32-color palette, mobile review UI and all four added portrait comparisons. PDF geometry tests check A4/Letter page boxes and the full-size image matrix on every tile. A physical printer was not used: users must print at 100% and measure the calibration ruler.

## Paired print-quality benchmark

Ten photos: six human subjects, two landscapes, one cat and one flower. Existing six-photo sources remain unchanged; four public-domain additions are pinned in portrait_sources.json. Manual eyes/nose/mouth boxes in portrait_annotations.json are evaluation-only. The added subjects were not used to select numerical merge thresholds. They are a small validation extension, not a statistically representative held-out dataset.

Compare both presets against version 1.2 pixel-mode geometry using identical Lab clusters, seed, smoothed input and detected protection masks per pair. The old helper defaults retain the 1.2 behavior; the renderer scratch-buffer change does not change output geometry. Main: 512 px, seed 12345, widths 20/40 cm (40 pairs). Second: 768 px, seed 42, widths 20/40 cm (40 pairs); stress: same 768 px inputs, widths 5/40 cm (40 pairs). Repeated 40 cm cases are not independent samples. Small original images are not upscaled. CPU OpenCV uses two threads; stage timings are diagnostic single samples, not a p95 or a speed guarantee.

| Maximum side / canvas width | Missing regions, pixels → physical | Mean unnumbered area % | Mean strong-edge recall % | Mean source DE76 | Minimum printed ink height mm |
| --- | ---: | ---: | ---: | ---: | ---: |
| 512 px / 20 cm | 3193 → 3156 | 12.203 → 11.472 | 81.434 → 81.431 | 6.384 → 6.368 | 3.516 → 3.516 |
| 512 px / 40 cm | 3193 → 3156 | 12.203 → 11.472 | 81.434 → 81.431 | 6.384 → 6.368 | 7.031 → 7.031 |
| 768 px / 20 cm | 5173 → 5091 | 10.350 → 9.542 | 81.523 → 81.523 | 6.307 → 6.286 | 2.344 → 2.344 |
| 768 px / 40 cm | 5173 → 5091 | 10.350 → 9.542 | 81.523 → 81.523 | 6.307 → 6.286 | 4.688 → 4.688 |
| 768 px / 5 cm | 5173 → 5395 | 10.350 → 23.342 | 81.523 → 81.825 | 6.307 → 6.917 | 0.586 → 2.000 |

At 20/40 cm, the change modestly improves coverage without changing mean human-eye metrics. At 5 cm, physical mode raises the smallest number ink from 0.586 mm to 2 mm, but missing area rises from 10.350% to 23.342% and color error increases. This is a readability constraint, not a universal image-quality improvement. Use a larger canvas/fewer colors and review unresolved details.

## Facial detail and limitations

- Zero detected/protected eye pixels changed palette ID in all pairs. This does not mean every real eye was detected or preserved.
- Main human-eye edge F1 stays 81.617%; eye DE76 stays 9.753. Added-subject nose F1 stays 58.425%, mouth F1 stays 71.418%.
- At 768 px / 5 cm, human-eye F1 changes 84.743% → 84.703%, eye DE76 8.898 → 8.994, nose F1 60.968% → 60.359%, mouth F1 stays 59.328%. At 40 cm these metrics stay unchanged.
- Cat eyes are reported separately: main F1 55.237% → 55.275%; 768 px / 5 cm F1 51.686% → 47.877%, eye DE76 9.571 → 10.011. The human detector does not protect animal eyes.
- Visual review confirms thinner Simple-mode templates at ordinary canvas sizes. It also shows lost unprotected eye detail on the small astronaut face in both modes. Glasses and frontal portraits do not cover profiles, heavy occlusion, harsh lighting or all skin appearances. Manual eye protection/editing remains the next important product gap.
- The initial experiment applying strict color guards to all tiny specks was rejected: the astronaut Detailed case grew from 457 to 3,999 missing regions. Final code retains the explicit noise cutoff and makes only additional number-fit geometry canvas-aware; it does not erase protected eyes to optimize counts.

## Reproduction

```sh
uv run --locked python -m pytest -q
uv run --locked python -m ruff check .
uv run --locked python -m benchmarks.run
uv run --locked python -m benchmarks.photos --download
uv run --locked python -m benchmarks.faces
uv run --locked python -m benchmarks.faces --size 768 --colors 16 --seeds 2026
uv run --locked python -m benchmarks.painting --download
uv run --locked python -m benchmarks.painting --size 768 --seed 42 --widths 5 40 --output .local/painting-small
```

Historical regression runners were also completed: six synthetic fixtures (legacy missing 247 → current 49), 18 RGB photo cases, 36 facial four-variant cases plus six second-size/seed cases. These retain their historical references and pixel-mode helper defaults; their old-vs-current gains must not be presented as gains introduced by release 1.3. Full JSON, images and diagnostic times remain local under ignored .local/release-1.3/.

## Next release

Manual protection masks and reversible region/number finishing, followed by save/reopen, offer the clearest user benefit. Binary result delivery remains an engineering follow-up (B14); broad portrait/pose evaluation, p95/memory profiling, accessibility review and storage/crash recovery remain incomplete. No new GPU, segmentation or vector-export capability is claimed.

A later verification attempt on the synced drive was interrupted when the drive unmounted (137 passes, three import failures). Restoring the Drive client, verifying all 4,583 copied files by SHA-256, and rerunning from the nonsynced folder produced the complete green result above. This was an environment interruption, not a passing run.
