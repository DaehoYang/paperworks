#!/usr/bin/env python3
"""Add AcroForm fields to the trip report PDF template."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


from .paths import TRIP_SOURCE_PDF, TRIP_TEMPLATE_PDF


SOURCE_PDF = TRIP_SOURCE_PDF
OUTPUT_PDF = TRIP_TEMPLATE_PDF

MULTILINE = 1 << 12


FIELDS = [
    ("과제번호", (135, 674, 298, 694), False),
    ("지원기관", (372, 674, 542, 694), False),
    ("연구책임자소속", (200, 652, 298, 672), False),
    ("연구책임자성명", (372, 652, 542, 672), False),
    ("연구기간", (135, 631, 298, 650), False),
    ("해당차수", (372, 631, 542, 650), False),
    ("연구과제명", (135, 609, 542, 629), False),
    ("출장자성명", (135, 565, 298, 586), False),
    ("참여구분", (372, 565, 542, 586), False),
    ("생년월일", (135, 544, 298, 564), False),
    ("지급계좌", (372, 544, 542, 564), False),
    ("출장목적", (135, 502, 542, 522), False),
    ("출장기간", (135, 480, 542, 500), False),
    ("여비구분", (135, 459, 298, 479), False),
    ("최종목적지", (372, 459, 542, 479), False),
    ("결과보고내용", (135, 230, 542, 456), True),
    ("작성일자", (467, 131, 535, 151), False),
    ("연구책임자서명", (507, 82, 535, 102), False),
]


def make_text_field(
    name: str,
    rect: tuple[float, float, float, float],
    multiline: bool,
    font_resource: str = "/F3",
) -> DictionaryObject:
    field = DictionaryObject()
    field.update(
        {
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/T"): TextStringObject(name),
            NameObject("/Rect"): ArrayObject([FloatObject(value) for value in rect]),
            NameObject("/F"): NumberObject(4),
            NameObject("/V"): TextStringObject(""),
            NameObject("/DV"): TextStringObject(""),
            NameObject("/DA"): TextStringObject(f"{font_resource} 10 Tf 0 g"),
            NameObject("/MK"): DictionaryObject(),
        }
    )
    if multiline:
        field[NameObject("/Ff")] = NumberObject(MULTILINE)
    return field


def add_trip_report_fields(source_pdf: Path = SOURCE_PDF, output_pdf: Path = OUTPUT_PDF) -> None:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    page = writer.pages[0]
    annotations = page.get("/Annots")
    if annotations is None:
        annotations = ArrayObject()
        page[NameObject("/Annots")] = annotations

    field_refs = ArrayObject()
    font_resource = "/F3"
    for name, rect, multiline in FIELDS:
        field = make_text_field(name, rect, multiline, font_resource=font_resource)
        field_ref = writer._add_object(field)
        annotations.append(field_ref)
        field_refs.append(field_ref)

    page_fonts = page.get("/Resources", {}).get("/Font", DictionaryObject())
    font = DictionaryObject({NameObject(font_resource): page_fonts[NameObject(font_resource)]})
    acroform = DictionaryObject(
        {
            NameObject("/Fields"): field_refs,
            NameObject("/NeedAppearances"): BooleanObject(False),
            NameObject("/DA"): TextStringObject(f"{font_resource} 10 Tf 0 g"),
            NameObject("/DR"): DictionaryObject({NameObject("/Font"): font}),
        }
    )
    writer._root_object.update({NameObject("/AcroForm"): writer._add_object(acroform)})

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


if __name__ == "__main__":
    add_trip_report_fields()
    print(OUTPUT_PDF)
