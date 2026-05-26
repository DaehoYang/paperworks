#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


PACKAGE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = Path(__file__).resolve().parents[3]
PURCHASE_DIR = WORKSPACE_DIR / "purchase"
ASSETS_DIR = PACKAGE_DIR / "assets"
DEFAULT_SOURCE = PURCHASE_DIR / "260401_optics" / "물품검수확인서.pdf"
DEFAULT_OUTPUT = ASSETS_DIR / "물품검수확인서_입력가능.pdf"

MULTILINE = 1 << 12
FONT_RESOURCE = "/F2"
DEFAULT_APPEARANCE = f"{FONT_RESOURCE} 10 Tf 0 g"

FIELDS = [
    ("품명1", (92, 638, 282, 656), False),
    ("품명2", (341, 638, 532, 656), False),
    ("품명3", (92, 391, 282, 409), False),
    ("품명4", (341, 391, 532, 409), False),
    ("검수년", (222, 124, 262, 143), False),
    ("검수월", (286, 124, 313, 143), False),
    ("검수일", (337, 124, 363, 143), False),
    ("검수자", (438, 103, 533, 123), False),
    ("메모", (51, 147, 540, 168), True),
]


def make_text_field(name: str, rect: tuple[float, float, float, float], multiline: bool) -> DictionaryObject:
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
            NameObject("/DA"): TextStringObject(DEFAULT_APPEARANCE),
            NameObject("/MK"): DictionaryObject(),
        }
    )
    if multiline:
        field[NameObject("/Ff")] = NumberObject(MULTILINE)
    return field


def add_inspection_fields(source_pdf: Path, output_pdf: Path, need_appearances: bool = True) -> None:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    page = writer.pages[0]
    annotations = page.get("/Annots")
    if annotations is None:
        annotations = ArrayObject()
        page[NameObject("/Annots")] = annotations

    field_refs = ArrayObject()
    for name, rect, multiline in FIELDS:
        field = make_text_field(name, rect, multiline)
        field_ref = writer._add_object(field)
        annotations.append(field_ref)
        field_refs.append(field_ref)

    page_fonts = page.get("/Resources", {}).get("/Font", DictionaryObject())
    if NameObject(FONT_RESOURCE) not in page_fonts:
        raise ValueError(f"{source_pdf} does not contain {FONT_RESOURCE} in page font resources")
    font = DictionaryObject({NameObject(FONT_RESOURCE): page_fonts[NameObject(FONT_RESOURCE)]})
    acroform = DictionaryObject(
        {
            NameObject("/Fields"): field_refs,
            NameObject("/NeedAppearances"): BooleanObject(need_appearances),
            NameObject("/DA"): TextStringObject(DEFAULT_APPEARANCE),
            NameObject("/DR"): DictionaryObject({NameObject("/Font"): font}),
        }
    )
    writer._root_object.update({NameObject("/AcroForm"): writer._add_object(acroform)})

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add editable fields to 물품검수확인서.pdf.")
    parser.add_argument("source_pdf", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--need-appearances", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    add_inspection_fields(args.source_pdf, args.output, need_appearances=args.need_appearances)
    print(args.output)


if __name__ == "__main__":
    main()
