"""Physical export geometry. Resizing a raster does not add printable detail."""
import math
from reportlab.lib.pagesizes import A4, letter, landscape

PAPERS = {"a4": A4, "letter": letter}


def canvas_metadata(width, height, params, placements=()):
    requested = params.get("canvas_width_cm", 0)
    if requested:
        width_mm = requested * 10
        height_mm = width_mm * height / width
        if height_mm > 2000:
            raise ValueError("The derived canvas height exceeds 200 cm. Choose a smaller width or crop the source image.")
    else:
        paper = PAPERS[params.get("paper_size", "a4")]
        page = landscape(paper) if width > height else paper
        scale = min((page[0] * 25.4 / 72 - 20) / width, (page[1] * 25.4 / 72 - 20) / height)
        width_mm, height_mm = width * scale, height * scale
    mm_per_pixel = width_mm / width
    # Report actual glyph ink height, excluding the renderer's safety padding.
    padding = params.get("stroke_width", 2) + 1
    minimum = min((max(0, item["box"][3] - 2 * padding) * mm_per_pixel for item in placements), default=None)
    warnings = []
    if minimum is not None and minimum < 2:
        warnings.append("Some printed numbers are smaller than 2 mm. Increase canvas width or simplify the image; inspect the PDF at actual size.")
    dpi = 25.4 / mm_per_pixel
    if requested and dpi < 150:
        warnings.append("The chosen canvas has fewer than 150 pixels per inch. Enlarging the export cannot create new detail; use a higher processing resolution if available.")
    return {"custom": bool(requested), "width_mm": round(width_mm, 3), "height_mm": round(height_mm, 3),
            "effective_dpi": round(dpi, 2), "minimum_number_height_mm": round(minimum, 3) if minimum is not None else None,
            "stroke_mm": round(params.get("stroke_width", 2) * mm_per_pixel, 3), "warnings": warnings}


def painting_geometry(width, height, params):
    """Translate print dimensions to bounded raster geometry, without upsampling."""
    if params.get("sizing_mode", "pixels") == "pixels":
        return dict(stroke_width=params["stroke_width"], font_scale=params["font_scale"],
                    area_min=params["area_min"], minimum_font_scale=None)
    from regions import label_glyph
    physical = canvas_metadata(width, height, params)
    mm_per_pixel = physical["width_mm"] / width
    minimum_pixels = math.ceil(2 / mm_per_pixel)
    stroke = max(1, math.floor(0.3 / mm_per_pixel + 0.5))
    # Bound glyph/kernel allocations for pathological image aspect ratios.
    if minimum_pixels > 256 or stroke > 32:
        raise ValueError("This canvas is too small for the processing resolution. Increase canvas width or reduce resolution.")
    scale = 0.3
    while min(label_glyph(c, scale).shape[0] for c in range(params["k"])) < minimum_pixels:
        scale = round(scale + 0.01, 2)
    return dict(stroke_width=stroke, font_scale=max(scale, round(scale * 1.4, 2)),
                area_min=params["area_min"], minimum_font_scale=scale)
