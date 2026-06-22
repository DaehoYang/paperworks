from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.paperwork.meeting import db, validation
from scripts.paperwork.meeting.models import Member, ReceiptRecord


class MeetingValidationTests(unittest.TestCase):
    def make_receipt(self, root: Path, **overrides: object) -> ReceiptRecord:
        receipt_path = root / str(overrides.pop("file_name", "receipt.jpg"))
        receipt_path.write_bytes(b"receipt")
        values = {
            "file_name": receipt_path.name,
            "receipt_path": receipt_path,
            "generated": datetime.now() - timedelta(hours=1),
            "total_price": 30000,
            "store_name": "테스트식당",
            "address": "서울시",
            "receipt_type": "restaurant",
            "status": "parsed",
            "ocr_engine": "manual",
        }
        values.update(overrides)
        return ReceiptRecord(**values)

    @patch("scripts.paperwork.meeting.config.members")
    @patch("scripts.paperwork.meeting.config.attendee_rules")
    def test_meeting_generation_input_rejects_missing_store_and_address(self, attendee_rules, members) -> None:
        attendee_rules.return_value = {"price_per_person": 30000, "min_attendees": 2, "max_attendees": 10}
        members.return_value = [Member("물리학과", "교수", "양대호"), Member("물리학과", "학생", "홍길동")]
        with tempfile.TemporaryDirectory() as td:
            record = self.make_receipt(Path(td), store_name="", address="")
            result = validation.validate_meeting_generation_input(record)
        self.assertFalse(result.ok)
        self.assertIn("meeting receipt must include store_name or address", result.errors)

    def test_trip_generation_input_accepts_valid_pair(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outbound = self.make_receipt(
                root,
                file_name="out.jpg",
                receipt_type="transport",
                origin="서울역",
                destination="대전역",
                generated=datetime(2026, 6, 20, 9, 0),
            )
            inbound = self.make_receipt(
                root,
                file_name="in.jpg",
                receipt_type="transport",
                origin="대전역",
                destination="서울역",
                generated=datetime(2026, 6, 20, 18, 0),
            )
            result = validation.validate_trip_generation_input(outbound, inbound)
        self.assertTrue(result.ok, result.errors)

    def test_generated_records_require_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            record = self.make_receipt(
                Path(td),
                status="generated",
                document_type="topic-a",
                output_pdf="meeting/output/missing.zip",
            )
            result = validation.validate_generated_records([record])
        self.assertFalse(result.ok)
        self.assertTrue(any("output file does not exist" in error for error in result.errors))

    def test_database_validation_detects_generated_receipt_without_link(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt = self.make_receipt(root, status="generated", document_type="", output_pdf="")
            db_path = root / "meeting.sqlite3"
            conn = db.connect(db_path)
            try:
                db.upsert_receipt(conn, receipt)
                conn.commit()
            finally:
                conn.close()
            result = validation.validate_database(db_path)
        self.assertFalse(result.ok)
        self.assertTrue(any("no generated_document link" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
