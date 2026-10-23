"""Image validation and the existing OpenCV processing pipeline."""
import base64
import io
import json
import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from regions import cleanup_regions, label_dimensions, label_glyph
from facial import detect_eyes, eye_mask, preserve_eye_pixels

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_PROCESS_PIXELS = 4_000_000
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP"}


class InvalidImage(ValueError):
    pass


def normalize_image(data: bytes, params: dict) -> bytes:
    if not data:
        raise InvalidImage("The image is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Images must be 20 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in SUPPORTED_FORMATS:
                    raise InvalidImage("Use a PNG, JPEG, WebP, or BMP image.")
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidImage("Use a still image instead of an animation.")
                width, height = image.size
                if width * height > MAX_INPUT_PIXELS:
                    raise InvalidImage("The source image must contain at most 40 million pixels.")
                scaled = (round(width * params["resolution_factor"]),
                          round(height * params["resolution_factor"]))
                if min(scaled) < 1 or scaled[0] * scaled[1] < params["k"]:
                    raise InvalidImage("The image is too small for this resolution and number of colors.")
                if scaled[0] * scaled[1] > MAX_PROCESS_PIXELS:
                    raise InvalidImage("Reduce Resolution Factor: processing is limited to 4 million pixels.")
                image.load()
                rgba = ImageOps.exif_transpose(image).convert("RGBA")
                rgb = Image.new("RGBA", rgba.size, "white")
                rgb.alpha_composite(rgba)
                output = io.BytesIO()
                rgb.convert("RGB").save(output, format="PNG")
                return output.getvalue()
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise InvalidImage("The file is not a readable image, or its dimensions are too large.") from exc


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


def extract_contours_and_label(labels, k, stroke_width, font_scale, detail_mask=None):
    """Keep every boundary and only draw numbers whose complete box fits inside."""
    height, width = labels.shape
    template = np.full((height, width, 3), 255, np.uint8)
    if detail_mask is not None and detail_mask.shape != labels.shape:
        raise ValueError("Detail mask must match the label map.")
    if detail_mask is not None:
        detail_mask = np.asarray(detail_mask, dtype=bool)
    placements = []
    unlabelled = 0
    for color_idx in range(k):
        mask = (labels == color_idx).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if detail_mask is None or not detail_mask.any() or stroke_width == 1:
            cv2.drawContours(template, contours, -1, (0, 0, 0), stroke_width)
        else:
            # A thick outline can black out the very details protected in the map.
            boundary = np.full((height, width), 255, np.uint8)
            cv2.drawContours(boundary, contours, -1, 0, stroke_width)
            thin = np.full_like(boundary, 255)
            cv2.drawContours(thin, contours, -1, 0, 1)
            boundary[detail_mask] = thin[detail_mask]
            np.minimum(template, boundary[:, :, None], out=template)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for component in range(1, count):
            x, y, w, h, _ = stats[component]
            region = (components[y:y+h, x:x+w] == component).astype(np.uint8)
            distance = cv2.distanceTransform(np.pad(region, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
            number_region = region.copy()
            if detail_mask is not None:
                number_region[detail_mask[y:y+h, x:x+w]] = 0
            found = False
            for scale in np.linspace(font_scale, min(font_scale, 0.3), 8):
                bw, bh, _, padding = label_dimensions(color_idx, stroke_width, scale)
                if bw > w or bh > h:
                    continue
                fits = cv2.erode(number_region, np.ones((bh, bw), np.uint8),
                                 borderType=cv2.BORDER_CONSTANT, borderValue=0)
                if not fits.any():
                    continue
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
    return template, placements, unlabelled


def process_job_worker(job_id, shared_state, directory, params):
    root = Path(directory)
    def progress(message, value):
        shared_state[job_id] = {"status": message, "progress": value}
    try:
        # Avoid every concurrent worker consuming all CPU threads.
        cv2.setNumThreads(2)
        cv2.ocl.setUseOpenCL(True)
        progress("Reading image", 5)
        image = cv2.imdecode(np.frombuffer((root / "input.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Could not decode the uploaded image.")
        factor = params["resolution_factor"]
        if factor < 1:
            progress("Scaling image", 10)
            image = cv2.resize(image, (round(image.shape[1] * factor), round(image.shape[0] * factor)),
                               interpolation=cv2.INTER_AREA)
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
        progress("Cleaning up small regions", 60)
        labels = cleanup_regions(raw_labels, params["k"], params["area_min"],
                                 centers, params["stroke_width"], params["font_scale"], protection)
        progress("Building preview image", 80)
        preview = centers[labels].astype(np.uint8)
        palette = [{"id": i + 1, "hex": "#{:02x}{:02x}{:02x}".format(*map(int, color)),
                    "rgb": list(map(int, color))} for i, color in enumerate(centers[:, ::-1])]
        progress("Generating printable template", 90)
        template, placements, missing = extract_contours_and_label(
            labels, params["k"], params["stroke_width"], params["font_scale"], protection)
        notices = []
        if missing:
            reason = " outside protected eye detail" if boxes else ""
            notices.append(f"{missing} region(s) could not fit a readable number{reason}. Try a smaller font or fewer colors; review fine details before painting.")
        if enabled and not boxes:
            notices.append("No human eyes were detected; eye protection was not applied. Review small, angled, or obscured faces.")
        elif boxes:
            notices.append(f"Protected {len(boxes)} detected eye area(s), using thin outlines and keeping numbers outside them. Fine eye regions may remain unnumbered.")
        result = {"processed_image": img_to_base64(preview), "template_image": img_to_base64(template),
                  "label_map": img_to_base64(labels.astype(np.uint8)), "palette": palette,
                  "image_dimensions": {"width": image.shape[1], "height": image.shape[0]},
                  "parameters": params, "label_placements": placements,
                  "eye_protection": {"enabled": enabled, "detected_faces": face_count, "boxes": boxes},
                  "warnings": notices}
        pending = root / "result.tmp"
        pending.write_text(json.dumps(result), encoding="utf-8")
        pending.replace(root / "result.json")
        progress("Complete", 100)
    except Exception as exc:
        shared_state[job_id] = {"status": "Error", "progress": 0, "error": str(exc)}
    finally:
        (root / "input.png").unlink(missing_ok=True)
