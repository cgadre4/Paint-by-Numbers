"""Image validation and the existing OpenCV processing pipeline."""
import base64
import io
import json
import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from regions import cleanup_regions, label_dimensions

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


def quantize_image(image, k, seed=None):
    """Keep the existing K-means settings; optional seed is for paired benchmarks."""
    if seed is not None:
        cv2.setRNGSeed(seed)
    pixels = image.reshape(-1, 3).astype(np.float32)
    _, flat_labels, centers = cv2.kmeans(pixels, k, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 10, cv2.KMEANS_RANDOM_CENTERS)
    return flat_labels.reshape(image.shape[:2]), centers


def extract_contours_and_label(labels, k, stroke_width, font_scale):
    """Keep every boundary and only draw numbers whose complete box fits inside."""
    height, width = labels.shape
    template = np.full((height, width, 3), 255, np.uint8)
    placements = []
    unlabelled = 0
    for color_idx in range(k):
        mask = (labels == color_idx).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(template, contours, -1, (0, 0, 0), stroke_width)
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for component in range(1, count):
            x, y, w, h, _ = stats[component]
            region = (components[y:y+h, x:x+w] == component).astype(np.uint8)
            distance = cv2.distanceTransform(np.pad(region, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
            text = str(color_idx + 1)
            found = False
            for scale in np.linspace(font_scale, min(font_scale, 0.3), 8):
                bw, bh, th, padding = label_dimensions(color_idx, stroke_width, scale)
                if bw > w or bh > h:
                    continue
                fits = cv2.erode(region, np.ones((bh, bw), np.uint8),
                                 borderType=cv2.BORDER_CONSTANT, borderValue=0)
                if not fits.any():
                    continue
                cy, cx = np.unravel_index(np.argmax(distance * fits), region.shape)
                left, top = int(x + cx - bw // 2), int(y + cy - bh // 2)
                cv2.putText(template, text, (left + padding, top + padding + th),
                            cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1, cv2.LINE_AA)
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
        progress("Applying bilateral filter", 20)
        smoothed = bilateral_filter(image, params["bilateral_diameter"], params["sigma_color"], params["sigma_color"])
        progress("Running K-means clustering", 40)
        raw_labels, centers = quantize_image(smoothed, params["k"])
        progress("Cleaning up small regions", 60)
        labels = cleanup_regions(raw_labels, params["k"], params["area_min"],
                                 centers, params["stroke_width"], params["font_scale"])
        progress("Building preview image", 80)
        preview = centers[labels].astype(np.uint8)
        palette = [{"id": i + 1, "hex": "#{:02x}{:02x}{:02x}".format(*map(int, color)),
                    "rgb": list(map(int, color))} for i, color in enumerate(centers[:, ::-1])]
        progress("Generating printable template", 90)
        template, placements, missing = extract_contours_and_label(
            labels, params["k"], params["stroke_width"], params["font_scale"])
        result = {"processed_image": img_to_base64(preview), "template_image": img_to_base64(template),
                  "label_map": img_to_base64(labels.astype(np.uint8)), "palette": palette,
                  "image_dimensions": {"width": image.shape[1], "height": image.shape[0]},
                  "parameters": params, "label_placements": placements,
                  "warnings": ([f"{missing} region(s) are too small to fit a readable number. Try fewer colors, a smaller font, or a larger minimum region area."] if missing else [])}
        pending = root / "result.tmp"
        pending.write_text(json.dumps(result), encoding="utf-8")
        pending.replace(root / "result.json")
        progress("Complete", 100)
    except Exception as exc:
        shared_state[job_id] = {"status": "Error", "progress": 0, "error": str(exc)}
    finally:
        (root / "input.png").unlink(missing_ok=True)
