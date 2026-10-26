"""Image validation and the existing OpenCV processing pipeline."""
import base64
import io
import json
import math
import logging
import os
import time
import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from cluster_cache import load_clusters
from regions import cleanup_regions, label_dimensions, label_glyph
from facial import detect_eyes, eye_mask, preserve_eye_pixels

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_PROCESS_PIXELS = 4_000_000
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP"}


class InvalidImage(ValueError):
    pass


def scaled_dimensions(width, height, factor):
    """Round positive dimensions half up, matching the browser's Math.round."""
    return math.floor(width * factor + 0.5), math.floor(height * factor + 0.5)


def decode_image(data: bytes, params: dict, *, resize=False):
    if not data:
        raise InvalidImage("The image is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Images must be 20 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(SUPPORTED_FORMATS)) as image:
                if image.format not in SUPPORTED_FORMATS:
                    raise InvalidImage("Use a PNG, JPEG, WebP, or BMP image.")
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidImage("Use a still image instead of an animation.")
                width, height = image.size
                if width * height > MAX_INPUT_PIXELS:
                    raise InvalidImage("The source image must contain at most 40 million pixels.")
                scaled = scaled_dimensions(width, height, params["resolution_factor"])
                if min(scaled) < 1 or scaled[0] * scaled[1] < params["k"]:
                    raise InvalidImage("The image is too small for this resolution and number of colors.")
                if scaled[0] * scaled[1] > MAX_PROCESS_PIXELS:
                    raise InvalidImage("Reduce Resolution Factor: processing is limited to 4 million pixels.")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                if resize and params["resolution_factor"] < 1:
                    if oriented.mode in {"P", "1"}:
                        oriented = oriented.convert("RGBA")
                    oriented = oriented.resize(scaled_dimensions(*oriented.size, params["resolution_factor"]), Image.Resampling.BOX)
                alpha = oriented.convert("RGBA").getchannel("A")
                profile = image.info.get("icc_profile")
                if profile:
                    try:
                        source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                        mode = "CMYK" if oriented.mode == "CMYK" else "RGB"
                        rgb = ImageCms.profileToProfile(oriented.convert(mode), source,
                                                       ImageCms.createProfile("sRGB"), outputMode="RGB")
                    except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                        raise InvalidImage("The embedded color profile is invalid. Export the image as sRGB and retry.") from exc
                else:
                    rgb = oriented.convert("RGB")
                white = Image.new("RGB", rgb.size, "white")
                white.paste(rgb, mask=alpha)
                return white
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise InvalidImage("The file is not a readable image, or its dimensions are too large.") from exc


def normalize_image(data: bytes, params: dict) -> bytes:
    """Compatibility helper for benchmarks; workers decode directly to pixels."""
    output = io.BytesIO()
    decode_image(data, params).save(output, format="PNG")
    return output.getvalue()


def img_to_base64(image: np.ndarray) -> str:
    # OpenCV's PNG encoder accepts BGR images and single-channel label maps.
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not encode the generated image.")
    return "data:image/png;base64," + base64.b64encode(encoded).decode("ascii")


def bilateral_filter(image, d, sigma_color, sigma_space):
    if cv2.ocl.useOpenCL():
        try:
            return cv2.bilateralFilter(cv2.UMat(image), d, sigma_color, sigma_space).get()
        except cv2.error:
            pass  # A driver can expose OpenCL without supporting this operation.
    return cv2.bilateralFilter(image, d, sigma_color, sigma_space)


def quantize_image(image, k, seed=None, color_space="rgb"):
    """K-means in RGB or float CIELab; always return BGR palette centers."""
    if color_space not in {"rgb", "lab"}:
        raise ValueError("Color space must be rgb or lab.")
    if seed is not None:
        cv2.setRNGSeed(seed)
    pixels = image.astype(np.float32)
    if color_space == "lab":
        pixels = cv2.cvtColor(pixels / 255.0, cv2.COLOR_BGR2Lab)
    # Scale the convergence tolerance with the Lab L range (0-100 vs 0-255).
    epsilon = 1.0 if color_space == "rgb" else 100.0 / 255.0
    _, flat_labels, centers = cv2.kmeans(pixels.reshape(-1, 3), k, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, epsilon),
        10, cv2.KMEANS_RANDOM_CENTERS)
    if color_space == "lab":
        centers = cv2.cvtColor(centers[None, :, :], cv2.COLOR_Lab2BGR)[0] * 255.0
        centers = np.clip(centers, 0, 255)
    return flat_labels.reshape(image.shape[:2]), centers


def extract_contours_and_label(labels, k, stroke_width, font_scale, detail_mask=None, missing_mask=None, *, minimum_font_scale=None):
    """Keep every boundary and only draw numbers whose complete box fits inside."""
    height, width = labels.shape
    template = np.full((height, width, 3), 255, np.uint8)
    if detail_mask is not None and detail_mask.shape != labels.shape:
        raise ValueError("Detail mask must match the label map.")
    if detail_mask is not None:
        detail_mask = np.asarray(detail_mask, dtype=bool)
    protected = detail_mask is not None and detail_mask.any()
    minimum_scale = min(font_scale, 0.3) if minimum_font_scale is None else minimum_font_scale
    if protected and stroke_width != 1:
        boundary = np.empty((height, width), np.uint8)
        thin = np.empty_like(boundary)
    placements = []
    unlabelled = 0
    for color_idx in range(k):
        mask = (labels == color_idx).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not protected or stroke_width == 1:
            cv2.drawContours(template, contours, -1, (0, 0, 0), stroke_width)
        else:
            # A thick outline can black out the very details protected in the map.
            boundary.fill(255)
            cv2.drawContours(boundary, contours, -1, 0, stroke_width)
            thin.fill(255)
            cv2.drawContours(thin, contours, -1, 0, 1)
            boundary[detail_mask] = thin[detail_mask]
            np.minimum(template, boundary[:, :, None], out=template)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for component in range(1, count):
            x, y, w, h, _ = stats[component]
            region = (components[y:y+h, x:x+w] == component).astype(np.uint8)
            distance = None
            number_region = region.copy()
            if detail_mask is not None:
                number_region[detail_mask[y:y+h, x:x+w]] = 0
            found = False
            for scale in np.linspace(max(font_scale, minimum_scale), minimum_scale, 8):
                bw, bh, _, padding = label_dimensions(color_idx, stroke_width, scale)
                if bw > w or bh > h:
                    continue
                fits = cv2.erode(number_region, np.ones((bh, bw), np.uint8),
                                 borderType=cv2.BORDER_CONSTANT, borderValue=0)
                if not fits.any():
                    continue
                if distance is None:
                    distance = cv2.distanceTransform(np.pad(region, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
                cy, cx = np.unravel_index(np.argmax(distance * fits), region.shape)
                left, top = int(x + cx - bw // 2), int(y + cy - bh // 2)
                glyph = label_glyph(color_idx, float(scale))
                gy, gx = top + padding, left + padding
                ink = 255 - glyph[:, :, None]
                target = template[gy:gy+glyph.shape[0], gx:gx+glyph.shape[1]]
                np.minimum(target, ink, out=target)
                placements.append({"id": color_idx + 1, "box": [left, top, bw, bh]})
                found = True
                break
            if not found:
                unlabelled += 1
                if missing_mask is not None:
                    missing_mask[y:y+h, x:x+w][region.astype(bool)] = 255
    return template, placements, unlabelled


def process_job_worker(job_id, shared_state, directory, params):
    root = Path(directory)
    timings = {}
    previous, stage = time.perf_counter(), None
    def progress(message, value):
        nonlocal previous, stage
        now = time.perf_counter()
        if stage:
            timings[stage] = round(now - previous, 4)
        previous, stage = now, message
        shared_state[job_id] = {"status": message, "progress": value, "schema_version": 2}
    try:
        # Avoid every concurrent worker consuming all CPU threads.
        cv2.setNumThreads(max(1, min(2, (os.cpu_count() or 2) // 2)))
        cv2.ocl.setUseOpenCL(False)
        progress("Reading image", 5)
        enabled = params.get("protect_eyes", True)
        cache = root / "clusters.npz"
        cached = None
        if cache.exists():
            with Image.open(root / "input.png") as header:
                size = header.size[::-1] if header.getexif().get(274) in {5, 6, 7, 8} else header.size
            expected = scaled_dimensions(*size, params["resolution_factor"])[::-1]
            cached = load_clusters(cache, params["k"], expected, eyes=True)
        reused = cached is not None
        if reused:
            progress("Reusing color analysis", 40)
            raw_labels, centers, protection, boxes, face_count = cached
        else:
            image = cv2.cvtColor(np.asarray(decode_image((root / "input.png").read_bytes(), params, resize=True)), cv2.COLOR_RGB2BGR)
            enabled = params.get("protect_eyes", True)
            boxes, face_count = [], 0
            if enabled:
                progress("Locating eyes", 15)
                boxes, face_count = detect_eyes(image)
            protection = eye_mask(image.shape, boxes)
            progress("Applying bilateral filter", 20)
            smoothed = bilateral_filter(image, params["bilateral_diameter"], params["sigma_color"], params["sigma_color"])
            smoothed = preserve_eye_pixels(image, smoothed, protection)
            space = params.get("color_space", "lab")
            progress("Clustering perceptual colors" if space == "lab" else "Clustering RGB colors", 40)
            raw_labels, centers = quantize_image(smoothed, params["k"], color_space=space)
            pending_cache = root / "clusters.tmp"
            with pending_cache.open("wb") as stream:
                np.savez(stream, labels=raw_labels, centers=centers, protection=protection,
                         boxes=np.asarray(boxes, dtype=np.int32).reshape(-1, 4), face_count=face_count)
            pending_cache.replace(cache)
        from canvas_size import canvas_metadata, painting_geometry
        geometry = painting_geometry(raw_labels.shape[1], raw_labels.shape[0], params)
        progress("Cleaning up small regions", 60)
        labels = cleanup_regions(raw_labels, params["k"], centers=centers,
                                 protected_mask=protection, **geometry)
        progress("Building preview image", 80)
        preview = centers[labels].astype(np.uint8)
        palette = [{"id": i + 1, "hex": "#{:02x}{:02x}{:02x}".format(*map(int, color)),
                    "rgb": list(map(int, color))} for i, color in enumerate(centers[:, ::-1])]
        progress("Generating printable template", 90)
        missing_mask = np.zeros(labels.shape, np.uint8)
        template, placements, missing = extract_contours_and_label(
            labels, params["k"], geometry["stroke_width"], geometry["font_scale"], protection, missing_mask,
            minimum_font_scale=geometry["minimum_font_scale"])
        notices = []
        if missing:
            reason = " outside protected eye detail" if boxes else ""
            notices.append(f"{missing} region(s) could not fit a readable number{reason}. Try fewer colors; review fine details against the color preview before painting.")
        if enabled and not boxes:
            notices.append("No human eyes were detected; eye protection was not applied. Review small, angled, or obscured faces.")
        elif boxes:
            notices.append(f"Protected {len(boxes)} detected eye area(s), using thin outlines and keeping numbers outside them. Fine eye regions may remain unnumbered.")
        physical = canvas_metadata(labels.shape[1], labels.shape[0], {**params, "stroke_width": geometry["stroke_width"]}, placements)
        physical["sizing_mode"] = params.get("sizing_mode", "pixels")
        notices.extend(physical.pop("warnings"))
        progress("Encoding result", 95)
        result = {"processed_image": img_to_base64(preview), "template_image": img_to_base64(template),
                  "label_map": img_to_base64(labels.astype(np.uint8)), "palette": palette,
                  "image_dimensions": {"width": labels.shape[1], "height": labels.shape[0]},
                  "parameters": params, "label_placements": placements,
                  "eye_protection": {"enabled": enabled, "detected_faces": face_count, "boxes": boxes},
                  "warnings": notices, "canvas": physical,
                  "missing_regions": {"count": missing, "area_percent": round(100 * np.count_nonzero(missing_mask) / missing_mask.size, 3),
                                      "mask": img_to_base64(missing_mask)},
                  "diagnostics": {"backend": "CPU", "threads": cv2.getNumThreads(), "reused_color_analysis": reused, "render_geometry": geometry, "stages_seconds": timings},
                  "schema_version": 2}
        pending = root / "result.tmp"
        pending.write_text(json.dumps(result), encoding="utf-8")
        pending.replace(root / "result.json")
        progress("Complete", 100)
    except Exception as exc:
        logging.getLogger(__name__).exception("Processing failed for job %s", job_id)
        shared_state[job_id] = {"status": "Error", "progress": 0, "error": str(exc) if isinstance(exc, (InvalidImage, ValueError)) else f"Processing failed. See the local server log for job {job_id}.",
                                "error_code": "invalid_image" if isinstance(exc, InvalidImage) else "processing_failed", "schema_version": 2}
    finally:
        try:
            (root / "input.png").unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("Input cleanup deferred to parent for job %s", job_id)
