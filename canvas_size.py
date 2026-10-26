"""Physical export geometry. Resizing a raster does not add printable detail."""
from reportlab.lib.pagesizes import A4, landscape


def canvas_metadata(width, height, params, placements=()):
    requested = params.get("canvas_width_cm", 0)
    if requested:
        width_mm = requested * 10
        height_mm = width_mm * height / width
        if height_mm > 2000:
            raise ValueError("The derived canvas height exceeds 200 cm. Choose a smaller width or crop the source image.")
    else:
        page = landscape(A4) if width > height else A4
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
