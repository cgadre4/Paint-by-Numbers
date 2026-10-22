# Initial cleanup results - 2026-10-22

Six seeded synthetic fixtures, three repeats per setting, two CPU threads, OpenCV 4.14.0. Both cleanup methods received identical quantization inputs and used the current four-connected renderer. These results do not establish photographic quality.

| Palette | Unnumbered before | Unnumbered after | Reduction | Small regions before/after |
| --- | ---: | ---: | ---: | ---: |
| 8 colors | 250 | 121 | 51.6% | 94/0 |
| 32 colors | 910 | 119 | 86.9% | 654/0 |

## 8 colors

Area minimum 50 pixels; stroke width 2; requested font scale 1; seed 12345.

| Fixture | Unnumbered before/after | RGB MAE before/after | Cleanup median ms before/after |
| --- | ---: | ---: | ---: |
| flat_shapes | 0/0 | 0.09/0.09 | 2.9/4.4 |
| landscape_gradient | 82/0 | 2.72/2.72 | 6.0/6.1 |
| portrait_details | 3/3 | 0.20/0.20 | 2.7/4.4 |
| textured_foliage | 123/98 | 3.32/3.61 | 15.9/10.5 |
| speckled_shapes | 6/2 | 5.92/5.76 | 82.2/22.7 |
| thin_contrast_lines | 36/18 | 0.47/0.29 | 21.2/14.4 |

## 32 colors

Area minimum 50 pixels; stroke width 2; requested font scale 1; seed 12345.

| Fixture | Unnumbered before/after | RGB MAE before/after | Cleanup median ms before/after |
| --- | ---: | ---: | ---: |
| flat_shapes | 0/0 | 0.09/0.09 | 10.7/14.1 |
| landscape_gradient | 368/6 | 2.62/2.63 | 521.0/548.3 |
| portrait_details | 12/4 | 0.17/0.16 | 31.3/29.9 |
| textured_foliage | 410/89 | 2.64/2.95 | 194.1/196.9 |
| speckled_shapes | 84/2 | 5.39/5.14 | 135.6/70.4 |
| thin_contrast_lines | 36/18 | 0.47/0.29 | 40.5/31.7 |

## Interpretation and verification

- Default portrait details were retained; unnumbered eyes/mouth were not removed just to improve the count. Foliage still trades some detail for fewer regions, visible in the RGB error and comparison PNGs.
- Timings are local measurements, not a universal speedup claim. The graph has overhead on simple images; some complex cases improved, while others were similar or slower.
- A four-million-pixel uniform input completed cleanup in 0.223 seconds. A 512x512 stress map with 65,536 components became 2,049 components in 1.120 seconds including component recounting. The former checks dimensions; it does not prove worst-case performance for a four-million-pixel textured photo.
- All 52 local tests passed, including browser/export/lifecycle workflows and 12 new region/benchmark checks. One existing Starlette/httpx deprecation warning remains. These measurements were recorded locally; remote CI results are tracked separately in GitHub Actions.
- The custom-image CLI path was exercised with a locally generated PNG and resizing to 192 pixels. Comparison images were visually inspected.
- Recreate full JSON timing samples, input hashes, and comparison images with the commands in README.md. Generated reports stay under ignored .local/; only this synthetic summary is part of the source tree.
- Next evaluation: run a varied set of actual photographs and judge detail preservation alongside unnumbered counts before tuning the guards further.
