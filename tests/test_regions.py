import json
import random

import cv2
import numpy as np
import pytest

from benchmarks.run import DEFAULTS, run_case, write_report
from pipeline import extract_contours_and_label, quantize_image
from regions import cleanup_regions, component_map, label_dimensions, label_fits


def test_merge_does_not_jump_across_a_border():
    labels = np.full((40, 40), 2, np.int32)
    labels[18:22, 18:22] = 1
    labels[19:21, 19:21] = 0
    centers = np.array([[10]*3, [250]*3, [15]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 10, centers)
    assert np.all(cleaned[19:21, 19:21] == 1)
    assert np.all(cleaned[:18] == 2)
    assert np.all(labels[19:21, 19:21] == 0)  # Input ownership preserved.


def test_neighbor_selection_balances_color_and_shared_boundary():
    labels = np.zeros((60, 60), np.int32)
    labels[:, 30:] = 1
    labels[28:32, 29:31] = 2
    centers = np.array([[20]*3, [220]*3, [30]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 10, centers)
    assert np.all(cleaned[28:32, 29:31] == 0)
    # Equal color distance: a longer shared edge determines the neighbor.
    labels[28:32, 29:31] = 0
    labels[:, 30:] = 1
    labels[15:18, 28:31] = 2
    centers = np.array([[20, 50, 80], [80, 50, 20], [50]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 10, centers)
    assert np.all(cleaned[15:18, 28:31] == 0)


def test_numberless_low_contrast_sliver_merges_but_detail_is_protected():
    labels = np.zeros((80, 80), np.int32)
    labels[35:38, 25:55] = 1  # Above area_min, but cannot contain a number.
    centers = np.array([[100]*3, [110]*3], np.float32)
    cleaned = cleanup_regions(labels, 2, 10, centers)
    assert np.all(cleaned == 0)
    _, placements, missing = extract_contours_and_label(cleaned, 2, 1, 0.5)
    assert len(placements) == 1 and missing == 0
    contrasting = np.array([[230]*3, [20]*3], np.float32)
    assert np.array_equal(cleanup_regions(labels, 2, 10, contrasting), labels)
    # Without a palette, extra fit-based merging cannot judge color distortion.
    assert np.array_equal(cleanup_regions(labels, 2, 10), labels)


def test_large_thin_features_are_not_erased_to_force_a_number():
    labels = np.zeros((100, 1200), np.int32)
    labels[45:50, 50:1150] = 1
    centers = np.array([[100]*3, [110]*3], np.float32)
    assert np.array_equal(cleanup_regions(labels, 2, 10, centers), labels)


def test_diagonal_contacts_are_separate_regions_and_receive_separate_numbers():
    labels = np.zeros((100, 100), np.int32)
    labels[10:40, 10:40] = 1
    labels[40:70, 40:70] = 1
    _, placements, missing = extract_contours_and_label(labels, 2, 1, 0.5)
    assert len([p for p in placements if p['id'] == 2]) == 2
    assert missing == 0


def test_bridge_coalesces_same_color_regions_and_stops():
    labels = np.zeros((70, 100), np.int32)
    labels[:, 49:51] = 1
    centers = np.array([[100]*3, [105]*3], np.float32)
    cleaned = cleanup_regions(labels, 2, 150, centers)
    assert np.all(cleaned == 0)
    assert np.array_equal(cleanup_regions(cleaned, 2, 150, centers), cleaned)


@pytest.mark.parametrize('shape', [(1, 1), (1, 100), (100, 1), (60, 80)])
def test_cleanup_handles_edges_and_uniform_or_fragmented_images(shape):
    rng = random.Random(45)
    labels = np.asarray(rng.choices(range(3), k=shape[0]*shape[1]), dtype=np.int32).reshape(shape)
    centers = np.array([[0]*3, [120]*3, [255]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 12, centers)
    assert cleaned.shape == labels.shape and cleaned.dtype == labels.dtype
    assert 0 <= cleaned.min() <= cleaned.max() < 3
    assert len(component_map(cleaned, 3)[1]) <= len(component_map(labels, 3)[1])
    assert np.array_equal(cleaned, cleanup_regions(labels, 3, 12, centers))
    uniform = np.zeros(shape, np.int32)
    assert np.array_equal(cleanup_regions(uniform, 3, 12, centers), uniform)


def test_double_digit_label_fit_agrees_with_rendering():
    bw, bh, _, _ = label_dimensions(11, 2, 0.3)
    labels = np.zeros((100, 120), np.int32)
    labels[20:20+bh, 20:20+bw] = 11
    region = np.ones((bh, bw), np.uint8)
    assert label_fits(region, 11, 2, 0.5)
    assert not label_fits(region[:, :-1], 11, 2, 0.5)
    _, placements, _ = extract_contours_and_label(labels, 12, 2, 0.5)
    assert any(p['id'] == 12 for p in placements)
    for p in placements:
        x, y, w, h = p['box']
        assert np.all(labels[y:y+h, x:x+w] == p['id']-1)


def test_seeded_benchmark_produces_repeatable_metrics_and_artifacts(tmp_path):
    source = np.full((64, 96, 3), 200, np.uint8)
    source[12:52, 20:76] = (40, 110, 170)
    source[25:28, 25:45] = (45, 115, 175)
    params = dict(DEFAULTS, k=3, area_min=20)
    a, centers_a = quantize_image(source, 3, seed=42)
    b, centers_b = quantize_image(source, 3, seed=42)
    assert np.array_equal(a, b) and np.array_equal(centers_a, centers_b)
    result = run_case('fixture', source, tmp_path, params=params, repeats=2, seed=42)
    report = write_report(tmp_path, [result], 42, 2)
    assert json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))['cases'][0]['metrics'] == result['metrics']
    assert (tmp_path/'fixture.png').is_file() and (tmp_path/'report.md').is_file()
    assert all(len(samples) == 2 and min(samples) >= 0 for samples in result['seconds_samples'].values())
    assert report['cases'][0]['metrics']['adjacent']['unnumbered_regions'] <= result['metrics']['legacy']['unnumbered_regions']


def test_equal_size_fragments_choose_similar_color_not_component_id():
    labels = np.full((40, 40), 2, np.int32)
    labels[18:20, 18:20] = 0
    labels[18:20, 20:22] = 1
    centers = np.array([[10]*3, [20]*3, [240]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 5, centers)
    assert np.all(cleaned[18:20, 18:22] == 1)
    assert np.all(cleaned[:18] == 2)


def test_extra_merge_preserves_strong_boundary_to_third_region():
    labels = np.zeros((50, 70), np.int32)
    labels[:, 35:38] = 1
    labels[:, 38:] = 2
    centers = np.array([[90]*3, [110]*3, [50]*3], np.float32)
    assert np.array_equal(cleanup_regions(labels, 3, 10, centers), labels)
    # A moderate-color merge can help when the strong boundary remains strong.
    centers = np.array([[90]*3, [118]*3, [200]*3], np.float32)
    cleaned = cleanup_regions(labels, 3, 10, centers)
    assert np.all(cleaned[:, :38] == 0)
    assert np.all(cleaned[:, 38:] == 2)


def test_all_digit_glyphs_include_antialiasing_and_fit_exact_boxes():
    from regions import label_glyph
    for color in range(32):
        for scale in (0.3, 0.5, 1.0, 2.0):
            glyph = label_glyph(color, scale)
            assert glyph.any() and not glyph.flags.writeable
            assert glyph[0].any() and glyph[-1].any()
            assert glyph[:, 0].any() and glyph[:, -1].any()
            # Independently rasterize on an oversized canvas: no ink was clipped.
            reference = np.zeros((150, 200), np.uint8)
            cv2.putText(reference, str(color+1), (20, 100), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, 255, 1, cv2.LINE_AA)
            x, y, w, h = cv2.boundingRect(reference)
            assert np.array_equal(glyph, reference[y:y+h, x:x+w])
        bw, bh, _, pad = label_dimensions(color, 2, 0.3)
        labels = np.full((bh, bw), color, np.int32)
        template, placements, missing = extract_contours_and_label(labels, 32, 2, 0.3)
        assert missing == 0 and placements == [{'id': color+1, 'box': [0, 0, bw, bh]}]
        assert np.array_equal(template[pad:bh-pad, pad:bw-pad, 0], 255-label_glyph(color, 0.3))
