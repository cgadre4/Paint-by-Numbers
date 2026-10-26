"""Downloads built from a completed job, independent of browser zoom/highlights."""
import base64
import io
import math

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import landscape
from canvas_size import PAPERS
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def image_bytes(data_url):
    return base64.b64decode(data_url.split(",", 1)[1], validate=True)


def palette_png(palette):
    columns, cell_width, cell_height = 4, 220, 130
    image = Image.new("RGB", (columns * cell_width + 40,
                              ((len(palette) + columns - 1) // columns) * cell_height + 90), "white")
    draw = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=24)
    body = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=14)
    draw.text((20, 15), "Color Palette Key", fill="black", font=title)
    for index, color in enumerate(palette):
        x, y = 20 + (index % columns) * cell_width, 65 + (index // columns) * cell_height
        draw.rectangle((x, y, x + 190, y + 48), fill=tuple(color["rgb"]), outline="black")
        draw.text((x, y + 53), f'{color["id"]}: {color["hex"].upper()}', fill="black", font=body)
        draw.text((x, y + 80), "RGB " + ", ".join(map(str, color["rgb"])), fill="black", font=small)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_export(result, kind, format_):
    if kind == "tiles":
        if format_ != "pdf":
            raise ValueError("Tiled templates are available as PDF only.")
        return tiled_pdf(result)
    if format_ == "png":
        return image_bytes(result["template_image"]) if kind == "template" else palette_png(result["palette"])
    output = io.BytesIO()
    dimensions = result["image_dimensions"]
    paper = PAPERS[result.get("parameters", {}).get("paper_size", "a4")]
    page = landscape(paper) if kind == "template" and dimensions["width"] > dimensions["height"] else paper
    physical = result.get("canvas", {})
    if kind == "template" and physical.get("custom"):
        page = (physical["width_mm"] * 72 / 25.4, physical["height_mm"] * 72 / 25.4)
    pdf = canvas.Canvas(output, pagesize=page, pageCompression=1)
    pdf.setTitle("Paint-by-Numbers " + kind.title())
    width, height = page
    margin = 0 if kind == "template" and physical.get("custom") else 28.35
    # Custom PDFs use exact canvas dimensions, printed at 100%; A4 retains 10 mm margins.
    if kind == "template":
        image = ImageReader(io.BytesIO(image_bytes(result["template_image"])))
        iw, ih = image.getSize()
        scale = min((width - 2 * margin) / iw, (height - 2 * margin) / ih)
        pdf.drawImage(image, (width - iw * scale) / 2, (height - ih * scale) / 2,
                      width=iw * scale, height=ih * scale)
    else:
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(margin, height - margin - 18, "Color Palette Key")
        cell_width = (width - 2 * margin) / 4
        for index, color in enumerate(result["palette"]):
            x = margin + (index % 4) * cell_width
            y = height - margin - 70 - (index // 4) * 85
            pdf.setFillColorRGB(*(channel / 255 for channel in color["rgb"]))
            pdf.rect(x, y, cell_width - 12, 25, fill=1, stroke=1)
            pdf.setFillColorRGB(0, 0, 0)
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(x, y - 14, f'{color["id"]}: {color["hex"].upper()}')
            pdf.setFont("Helvetica", 8)
            pdf.drawString(x, y - 27, "RGB " + ", ".join(map(str, color["rgb"])))
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def tile_layout(width_mm, height_mm, paper_size="a4"):
    """Use the orientation requiring fewest sheets, with 10 mm margins/5 mm overlap."""
    paper = PAPERS[paper_size]
    layouts = []
    for page in (paper, landscape(paper)):
        usable = tuple(value * 25.4 / 72 - 20 for value in page)
        cols = max(1, math.ceil((width_mm - 5 - 1e-9) / (usable[0] - 5)))
        rows = max(1, math.ceil((height_mm - 5 - 1e-9) / (usable[1] - 5)))
        layouts.append((cols * rows, page, usable, cols, rows))
    count, page, usable, cols, rows = min(layouts, key=lambda x: x[0])
    if count > 100:
        raise ValueError("This canvas needs more than 100 tiled sheets. Reduce canvas width or use the exact-size PDF.")
    return dict(page=page, usable=usable, columns=cols, rows=rows, pages=count, overlap_mm=5)


def tiled_pdf(result):
    physical = result["canvas"]
    width_mm, height_mm = physical["width_mm"], physical["height_mm"]
    layout = tile_layout(width_mm, height_mm, result.get("parameters", {}).get("paper_size", "a4"))
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=layout["page"], pageCompression=1)
    pdf.setTitle("Paint-by-Numbers tiled template - print at 100%")
    image = ImageReader(io.BytesIO(image_bytes(result["template_image"])))
    unit = 72 / 25.4
    pw, ph = (v / unit for v in layout["page"])
    uw, uh = layout["usable"]
    for row in range(layout["rows"]):
        for col in range(layout["columns"]):
            ox, oy = col * (uw - 5), row * (uh - 5)
            pdf.saveState()
            pdf.scale(unit, unit)
            path = pdf.beginPath()
            path.rect(10, 10, uw, uh)
            pdf.clipPath(path, stroke=0)
            pdf.drawImage(image, 10 - ox, ph - 10 - height_mm + oy, width_mm, height_mm)
            # Identical crosses in neighboring overlap bands align at actual size.
            pdf.setLineWidth(0.12)
            xs = [i * (uw - 5) + 2.5 for i in range(1, layout["columns"])]
            ys = [i * (uh - 5) + 2.5 for i in range(1, layout["rows"])]
            marks = [(x, y) for x in xs for y in range(25, math.ceil(height_mm), 50)]
            marks += [(x, y) for y in ys for x in range(25, math.ceil(width_mm), 50)]
            for x, y in marks:
                if ox <= x <= ox + uw and oy <= y <= oy + uh:
                    px, py = 10 + x - ox, ph - 10 - y + oy
                    pdf.line(px-1.5, py, px+1.5, py)
                    pdf.line(px, py-1.5, px, py+1.5)
            pdf.restoreState()
            pdf.setLineWidth(0.4)
            right = (10 + min(uw, width_mm - ox)) * unit
            bottom = (ph - 10 - min(uh, height_mm - oy)) * unit
            for x, direction in ((10*unit, -1), (right, 1)):
                for y, vertical in (((ph-10)*unit, 1), (bottom, -1)):
                    pdf.line(x+direction*unit, y, x+direction*3*unit, y)
                    pdf.line(x, y+vertical*unit, x, y+vertical*3*unit)
            pdf.setFont("Helvetica", 8)
            pdf.drawString(10*unit, (ph-6)*unit, f"Row {row+1}/{layout['rows']} - column {col+1}/{layout['columns']} | Print 100%, no page fitting | 5 mm overlap")
            pdf.drawString(10*unit, 5*unit, "Trim margins; align matching crosses in overlapping strips.")
            pdf.setLineWidth(0.5)
            x, y = (pw-65)*unit, 5*unit
            pdf.line(x, y, x+50*unit, y)
            for tick in range(0, 51, 10):
                pdf.line(x+tick*unit, y-1*unit, x+tick*unit, y+1*unit)
            pdf.drawCentredString(x+25*unit, y+2*unit, "50 mm calibration")
            pdf.showPage()
    pdf.save()
    return output.getvalue()
