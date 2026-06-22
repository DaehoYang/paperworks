from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.paperwork.meeting import process_receipts
from scripts.paperwork.meeting.models import ReceiptRecord


class ArchiveProcessedReceiptsTests(unittest.TestCase):
    def make_record(self, path: Path, output_pdf: str) -> ReceiptRecord:
        return ReceiptRecord(
            file_name=path.name,
            receipt_path=path,
            generated=datetime(2026, 6, 21, 12, 0, 0),
            total_price=1000,
            status="generated",
            document_type="meeting",
            output_pdf=output_pdf,
        )

    def test_archives_receipt_with_output_document_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            used = root / "used"
            receipt = root / "Image.jpg"
            receipt.write_bytes(b"jpg")
            (used / "Image.jpg").parent.mkdir(parents=True)
            (used / "Image.jpg").write_bytes(b"old")

            record = self.make_record(receipt, "output/260603_1900_회의록.zip")
            with patch.object(process_receipts, "USED_RECEIPT_DIR", used):
                archived = process_receipts.archive_processed_receipts([record])

            self.assertEqual(archived["Image.jpg"], used / "260603_1900_회의록.jpg")
            self.assertTrue((used / "260603_1900_회의록.jpg").exists())
            self.assertTrue((used / "Image.jpg").exists())

    def test_archives_multiple_receipts_for_same_output_with_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            used = root / "used"
            outbound = root / "out.png"
            inbound = root / "in.jpg"
            outbound.write_bytes(b"png")
            inbound.write_bytes(b"jpg")

            records = [
                self.make_record(outbound, "output/260603_출장보고서.zip"),
                self.make_record(inbound, "output/260603_출장보고서.zip"),
            ]
            with patch.object(process_receipts, "USED_RECEIPT_DIR", used):
                archived = process_receipts.archive_processed_receipts(records)

            self.assertEqual(archived["out.png"], used / "260603_출장보고서_1.png")
            self.assertEqual(archived["in.jpg"], used / "260603_출장보고서_2.jpg")
            self.assertTrue((used / "260603_출장보고서_1.png").exists())
            self.assertTrue((used / "260603_출장보고서_2.jpg").exists())


if __name__ == "__main__":
    unittest.main()
