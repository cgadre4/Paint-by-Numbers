"""Offline regression coverage for perceptual clustering and eye detail."""

import base64
import json
import random

import cv2
import numpy as np
import pytest

import facial
import pipeline
from benchmarks.faces import color_metrics, annotation_masks
from regions import cleanup_regions


def eye_fixture():
    labels = np.zeros((100, 180), np.int32)
    boxes = [[35, 30, 24, 18], [115, 30, 24, 18]]
    for x, y, w, h in boxes:
        labels[y + 4 : y + 14, x + 6 : x + 16] = 1
        labels[y + 6 : y + 9, x + 10 : x + 13] = 2
    return labels, np.array([[180, 195, 220], [20] * 3, [250] * 3], np.float32), boxes


@pytest.mark.parametrize("space", ["rgb", "lab"])
def test_color_clustering_roundtrip_palette_and_seed(space):
    source = np.zeros((40, 60, 3), np.uint8)
    source[:, :20] = (20, 30, 240)
    source[:, 20:40] = (240, 30, 20)
    source[:, 40:] = (30, 230, 30)
    labels, centers = pipeline.quantize_image(source, 3, seed=23, color_space=space)
    again, other_centers = pipeline.quantize_image(
        source, 3, seed=23, color_space=space
    )
    assert labels.shape == source.shape[:2] and labels.dtype == np.int32
    assert centers.shape == (3, 3) and np.isfinite(centers).all()
    assert 0 <= centers.min() <= centers.max() <= 255
    assert np.array_equal(labels, again) and np.array_equal(centers, other_centers)
    assert np.max(np.abs(centers[labels] - source.astype(float))) < 2
    assert labels[10, 5] != labels[10, 30] != labels[10, 50]


def test_invalid_color_space_is_rejected():
    with pytest.raises(ValueError, match="Color space"):
        pipeline.quantize_image(
            np.zeros((10, 10, 3), np.uint8), 3, color_space="unknown"
        )


def test_balanced_eye_smoothing_reduces_noise_without_erasing_contrast():
    labels, centers, boxes = eye_fixture()
    source = centers[labels].astype(np.uint8)
    noise = np.random.default_rng(42).integers(-18, 19, source.shape)
    source = np.clip(source.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    source[35:38, 45:48] = 250
    source[40:43, 42:45] = 20
    original = source.copy()
    mask = facial.eye_mask(labels.shape, boxes)
    filtered = np.full_like(source, 5)
    fine = facial.preserve_eye_pixels(source, filtered, mask, detail="fine")
    balanced = facial.preserve_eye_pixels(source, filtered, mask, detail="balanced")
    assert (
        balanced[30:33, 35:59].astype(float).std(axis=(0, 1)).mean()
        < fine[30:33, 35:59].astype(float).std(axis=(0, 1)).mean()
    )
    assert balanced[36, 46].mean() - balanced[41, 43].mean() > 180
    assert np.array_equal(balanced[~mask], filtered[~mask])
    assert np.array_equal(source, original) and np.all(filtered == 5)
    with pytest.raises(ValueError, match="Eye detail"):
        facial.preserve_eye_pixels(source, filtered, mask, detail="unknown")


@pytest.mark.parametrize(
    "detail,diameter,sigma", [("fine", 3, 25), ("balanced", 5, 40)]
)
def test_eye_filter_crop_halo_matches_full_frame(detail, diameter, sigma):
    source = np.random.default_rng(7).integers(0, 256, (80, 120, 3), np.uint8)
    for box in [[0, 0, 25, 20], [30, 20, 40, 30], [95, 60, 25, 20]]:
        mask = facial.eye_mask(source.shape, [box])
        actual = facial.preserve_eye_pixels(
            source, np.zeros_like(source), mask, detail=detail
        )
        expected = cv2.bilateralFilter(source, diameter, sigma, sigma)
        assert np.array_equal(actual[mask], expected[mask])
        assert not actual[~mask].any()


def test_missing_diagnostics_separate_protected_detail_without_hiding_total():
    labels = np.zeros((90, 160), np.uint8)
    labels[20:22, 20:22] = 1
    labels[40:42, 70:72] = 2
    mask = np.zeros(labels.shape, bool)
    mask[20, 20] = True
    details = {}
    missing = np.zeros(labels.shape, np.uint8)
    template, placements, count = pipeline.extract_contours_and_label(
        labels, 3, 1, 0.5, mask, missing, missing_details=details
    )
    reference, reference_placements, reference_count = (
        pipeline.extract_contours_and_label(labels, 3, 1, 0.5, mask)
    )
    assert np.array_equal(template, reference) and placements == reference_placements
    assert count == reference_count == 2
    assert details["protected_count"] == details["unprotected_count"] == 1
    assert details["protected_area_percent"] + details[
        "unprotected_area_percent"
    ] == pytest.approx(100 * np.count_nonzero(missing) / missing.size, abs=0.002)


def test_protection_preserves_both_pupils_and_highlights_below_area_cutoff():
    labels, centers, boxes = eye_fixture()
    mask = facial.eye_mask(labels.shape, boxes)
    cleaned = cleanup_regions(labels, 3, 500, centers, protected_mask=mask)
    assert np.array_equal(cleaned[mask], labels[mask])
    assert not np.array_equal(
        cleanup_regions(labels, 3, 500, centers)[mask], labels[mask]
    )
    assert np.array_equal(
        cleanup_regions(labels, 3, 50, centers),
        cleanup_regions(labels, 3, 50, centers, protected_mask=np.zeros_like(mask)),
    )
    with pytest.raises(ValueError, match="Protection mask"):
        cleanup_regions(labels, 3, 50, centers, protected_mask=np.zeros((1, 1)))


@pytest.mark.parametrize("seed", [1, 17, 93])
def test_protected_pixels_survive_graph_coalescing(seed):
    rng = random.Random(seed)
    labels = np.array(rng.choices(range(4), k=40 * 50), np.int32).reshape(40, 50)
    mask = np.zeros(labels.shape, bool)
    mask[10:15, 10:15] = True
    centers = np.array([[20] * 3, [25] * 3, [120] * 3, [230] * 3], np.float32)
    cleaned = cleanup_regions(labels, 4, 50, centers, protected_mask=mask)
    assert np.array_equal(cleaned[mask], labels[mask])
    assert np.array_equal(
        cleanup_regions(labels, 4, 50, centers, protected_mask=np.ones_like(mask)),
        labels,
    )


def test_eye_template_keeps_thin_contours_and_numbers_outside_eyes():
    labels, centers, boxes = eye_fixture()
    mask = facial.eye_mask(labels.shape, boxes)
    thick, _, _ = pipeline.extract_contours_and_label(labels, 3, 4, 0.5)
    protected, placements, missing = pipeline.extract_contours_and_label(
        labels, 3, 4, 0.5, mask
    )
    thin = np.full(labels.shape, 255, np.uint8)
    for color in range(3):
        contours, _ = cv2.findContours(
            (labels == color).astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(thin, contours, -1, 0, 1)
    assert np.array_equal(protected[:, :, 0][mask], thin[mask])
    assert (protected[:, :, 0][mask] == 255).sum() > (thick[:, :, 0][mask] == 255).sum()
    assert missing > 0
    for p in placements:
        x, y, w, h = p["box"]
        assert not mask[y : y + h, x : x + w].any()
        assert np.all(labels[y : y + h, x : x + w] == p["id"] - 1)
    with pytest.raises(ValueError, match="Detail mask"):
        pipeline.extract_contours_and_label(labels, 3, 2, 1, np.zeros((1, 1)))


def test_mild_eye_filter_changes_only_mask_and_keeps_high_contrast():
    labels, centers, boxes = eye_fixture()
    source = centers[labels].astype(np.uint8)
    filtered = np.full_like(source, 100)
    mask = facial.eye_mask(labels.shape, boxes)
    result = facial.preserve_eye_pixels(source, filtered, mask)
    assert np.array_equal(result[~mask], filtered[~mask])
    assert result[36, 45].mean() > result[35, 43].mean() + 100
    assert np.all(filtered == 100)  # Do not mutate the regular filtered image.


def test_bundled_detectors_load_and_find_no_eyes_in_blank_image():
    assert facial.detect_eyes(np.full((100, 160, 3), 200, np.uint8)) == ([], 0)


def test_detector_scales_eye_boxes_back_to_processing_coordinates(monkeypatch):
    class Cascade:
        def __init__(self, path):
            self.face = "frontalface" in path

        def empty(self):
            return False

        def detectMultiScale(self, image, *args, **kwargs):
            if self.face:
                assert image.shape == (480, 960)
                return np.array([[100, 80, 200, 180]])
            assert image.shape == (117, 200)
            return np.array([[20, 40, 30, 30], [120, 40, 30, 30]])

    monkeypatch.setattr(facial.cv2, "CascadeClassifier", Cascade)
    boxes, count = facial.detect_eyes(np.zeros((1000, 2000, 3), np.uint8))
    assert count == 1 and boxes == [[250, 250, 63, 63], [458, 250, 63, 63]]
    mask = facial.eye_mask((1000, 2000), boxes)
    assert mask[250, 250] and mask[250, 458]


def test_missing_detector_is_reported_not_silently_ignored(monkeypatch):
    class Missing:
        def empty(self):
            return True

    monkeypatch.setattr(facial.cv2, "CascadeClassifier", lambda path: Missing())
    with pytest.raises(RuntimeError, match="detector is unavailable"):
        facial.detect_eyes(np.zeros((100, 100, 3), np.uint8))


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("detail", ["fine", "balanced"])
def test_worker_applies_eye_options_and_returns_diagnostics(
    tmp_path, monkeypatch, params, enabled, detail
):
    labels, centers, boxes = eye_fixture()
    source = centers[labels].astype(np.uint8)
    cv2.imwrite(str(tmp_path / "input.png"), source)

    def detect(image):
        assert enabled, "Detection should be skipped when disabled"
        return boxes, 1

    monkeypatch.setattr(pipeline, "detect_eyes", detect)
    params.update(
        k=3, area_min=500, color_space="lab", protect_eyes=enabled, eye_detail=detail
    )
    progress = {}
    pipeline.process_job_worker("job", progress, str(tmp_path), params)
    assert progress["job"]["status"] == "Complete", progress
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["parameters"]["color_space"] == "lab"
    assert result["parameters"]["eye_detail"] == detail
    assert result["eye_protection"]["boxes"] == (boxes if enabled else [])
    assert result["eye_protection"]["enabled"] == enabled
    assert not (tmp_path / "input.png").exists()
    image = cv2.imdecode(
        np.frombuffer(base64.b64decode(result["label_map"].split(",")[1]), np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    if enabled:
        assert image[35, 43] != image[36, 45]  # Pupil and highlight stay separate.
        mask = facial.eye_mask(labels.shape, boxes)
        for p in result["label_placements"]:
            x, y, w, h = p["box"]
            assert not mask[y : y + h, x : x + w].any()
        assert any("Protected 2" in warning for warning in result["warnings"])


def test_facial_metrics_use_common_source_and_scaled_manual_annotations():
    source = np.full((60, 100, 3), 100, np.uint8)
    source[20:35, 30:45] = 230
    annotation = dict(size=[200, 120], eyes=[[60, 40, 30, 30]], face=[20, 20, 140, 80])
    eyes, face, box = annotation_masks(source.shape, annotation)
    assert eyes.sum() == 225 and box == [10, 10, 70, 40]
    exact = color_metrics(source, source, eyes, face)
    damaged = color_metrics(source, np.full_like(source, 100), eyes, face)
    assert exact["source_delta_e76"] == exact["eye_delta_e76"] == 0
    assert damaged["eye_delta_e76"] > 0
    assert damaged["face_edges"]["recall"] < exact["face_edges"]["recall"]


def test_face_benchmark_reports_all_four_variants_offline(tmp_path, monkeypatch):
    from benchmarks import faces
    from benchmarks.run import DEFAULTS

    labels, centers, boxes = eye_fixture()
    source = centers[labels].astype(np.uint8)
    monkeypatch.setattr(faces, "detect_eyes", lambda image: (boxes, 1))
    annotation = dict(size=[180, 100], eyes=boxes, face=[10, 10, 160, 80])
    result = faces.run_case(
        {"id": "fixture", "category": "portrait"},
        source,
        dict(DEFAULTS, k=3),
        tmp_path,
        annotation,
        seed=42,
    )
    assert set(result["metrics"]) == {"rgb", "lab", "rgb_eyes", "lab_eyes"}
    for name in ("rgb_eyes", "lab_eyes"):
        assert result["metrics"][name]["detected_eye_pixels_changed_by_cleanup"] == 0
    report = dict(
        baseline_commit="6b657ba",
        size=180,
        seeds=[42],
        opencv=cv2.__version__,
        cases=[result],
    )
    faces.write_report(tmp_path, report)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report
    assert "Eye edge F1" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (tmp_path / "fixture-3-42.png").is_file()
