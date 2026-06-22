from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from scripts.paperwork.meeting import pdf_utils


class ZipOutputTests(unittest.TestCase):
    def test_write_document_receipt_zip_uses_document_and_img_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            document = root / "document.pdf"
            document.write_bytes(b"%PDF-1.4\n%%EOF\n")
            receipt = root / "receipt.png"
            Image.new("RGB", (2400, 1200), "white").save(receipt)
            output = root / "260620_1130_회의록.zip"

            pdf_utils.write_document_receipt_zip(document, [receipt], output, "회의록.pdf")

            with ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(names, ["회의록.pdf", "img.jpg"])
                self.assertLess(len(archive.read("img.jpg")), pdf_utils.MAX_RECEIPT_IMAGE_BYTES)

    def test_write_document_receipt_zip_numbers_multiple_images(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            document = root / "document.pdf"
            document.write_bytes(b"%PDF-1.4\n%%EOF\n")
            receipts = []
            for name in ("out.png", "in.png"):
                receipt = root / name
                Image.new("RGB", (100, 100), "white").save(receipt)
                receipts.append(receipt)
            output = root / "260620_출장보고서.zip"

            pdf_utils.write_document_receipt_zip(document, receipts, output, "출장보고서.pdf")

            with ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["출장보고서.pdf", "img1.jpg", "img2.jpg"])


if __name__ == "__main__":
    unittest.main()
