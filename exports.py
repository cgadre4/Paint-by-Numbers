"""Downloads built from a completed job, independent of browser zoom/highlights."""
import base64
import io

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4, landscape
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
    if format_ == "png":
        return image_bytes(result["template_image"]) if kind == "template" else palette_png(result["palette"])
    output = io.BytesIO()
    dimensions = result["image_dimensions"]
    page = landscape(A4) if kind == "template" and dimensions["width"] > dimensions["height"] else A4
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
