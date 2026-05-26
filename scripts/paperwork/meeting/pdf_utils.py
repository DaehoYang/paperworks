from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


MAX_RECEIPT_IMAGE_BYTES = 1_000_000
RECEIPT_IMAGE_LONG_EDGE = 1800
MIN_RECEIPT_IMAGE_LONG_EDGE = 900
JPEG_QUALITIES = (85, 78, 70, 62, 54, 46)


def register_korean_font() -> str:
    for font_name in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))
        except Exception:
            pass
    return "HYGothic-Medium"


def compressed_receipt_image(image: Image.Image, max_bytes: int = MAX_RECEIPT_IMAGE_BYTES) -> BytesIO:
    working = image.copy()
    long_edge = max(working.size)
    if long_edge > RECEIPT_IMAGE_LONG_EDGE:
        scale = RECEIPT_IMAGE_LONG_EDGE / long_edge
        working = working.resize((int(working.width * scale), int(working.height * scale)), Image.LANCZOS)
        long_edge = max(working.size)

    while True:
        for quality in JPEG_QUALITIES:
            buffer = BytesIO()
            working.save(buffer, format="JPEG", quality=quality, optimize=True)
            if buffer.tell() <= max_bytes or quality == JPEG_QUALITIES[-1] and long_edge <= MIN_RECEIPT_IMAGE_LONG_EDGE:
                buffer.seek(0)
                return buffer
        long_edge = max(MIN_RECEIPT_IMAGE_LONG_EDGE, int(long_edge * 0.85))
        scale = long_edge / max(working.size)
        working = working.resize((max(1, int(working.width * scale)), max(1, int(working.height * scale))), Image.LANCZOS)


def image_page(receipt_path: Path) -> BytesIO:
    image = ImageOps.exif_transpose(Image.open(receipt_path)).convert("RGB")
    page_width, page_height = A4
    max_width = page_width - 72
    max_height = page_height - 72
    scale = min(max_width / image.width, max_height / image.height)
    width = image.width * scale
    height = image.height * scale
    x = (page_width - width) / 2
    y = (page_height - height) / 2

    packet = BytesIO()
    pdf_canvas = canvas.Canvas(packet, pagesize=A4)
    pdf_canvas.drawImage(ImageReader(compressed_receipt_image(image)), x, y, width=width, height=height)
    pdf_canvas.save()
    packet.seek(0)
    return packet


def combine_pdfs_and_receipts(report_pdf: Path, receipts: list[Path], output_pdf: Path) -> None:
    report_reader = PdfReader(str(report_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(report_reader)
    for receipt in receipts:
        if receipt.suffix.lower() == ".pdf":
            for page in PdfReader(str(receipt)).pages:
                writer.add_page(page)
        else:
            for page in PdfReader(image_page(receipt)).pages:
                writer.add_page(page)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def fill_form(template_pdf: Path, output_pdf: Path, values: dict[str, str]) -> None:
    reader = PdfReader(str(template_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(writer.pages[0], values, auto_regenerate=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)
