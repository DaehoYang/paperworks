#!/usr/bin/env python3
"""Normalize the meeting minutes PDF form for Korean field editing."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, TextStringObject


from .paths import ASSETS_DIR


SOURCE_PDF = ASSETS_DIR / "바나연회의록_빈칸.pdf"
OUTPUT_PDF = ASSETS_DIR / "바나연회의록_입력가능.pdf"
FONT_RESOURCE = "/C2_2"
DEFAULT_APPEARANCE = f"{FONT_RESOURCE} 10 Tf 0 g"


def normalize_minutes_form(source_pdf: Path = SOURCE_PDF, output_pdf: Path = OUTPUT_PDF) -> None:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    acroform = writer._root_object.get(NameObject("/AcroForm"))
    if acroform is None:
        raise ValueError(f"{source_pdf} has no AcroForm")
    acroform = acroform.get_object()
    acroform[NameObject("/NeedAppearances")] = BooleanObject(False)
    acroform[NameObject("/DA")] = TextStringObject(DEFAULT_APPEARANCE)
    page_fonts = writer.pages[0].get("/Resources", {}).get("/Font", DictionaryObject())
    if NameObject(FONT_RESOURCE) not in page_fonts:
        raise ValueError(f"{source_pdf} has no {FONT_RESOURCE} font resource")
    acroform[NameObject("/DR")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject(FONT_RESOURCE): page_fonts[NameObject(FONT_RESOURCE)]})}
    )

    for page in writer.pages:
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                annot[NameObject("/DA")] = TextStringObject(DEFAULT_APPEARANCE)

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


if __name__ == "__main__":
    normalize_minutes_form()
    print(OUTPUT_PDF)
