from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.paperwork.meeting import db
from scripts.paperwork.meeting.models import ReceiptRecord


class MeetingDbTests(unittest.TestCase):
    def test_sync_records_creates_receipt_and_generated_document(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt_path = root / "receipt.jpg"
            receipt_path.write_bytes(b"receipt")
            db_path = root / "meeting.sqlite3"
            record = ReceiptRecord(
                file_name="receipt.jpg",
                receipt_path=receipt_path,
                generated=datetime(2026, 6, 20, 12, 30),
                total_price=33000,
                store_name="테스트식당",
                address="서울시",
                receipt_type="restaurant",
                status="generated",
                document_type="topic-a",
                output_pdf="meeting/output/260620_1130_회의록.pdf",
                ocr_engine="manual",
            )

            db.sync_records(
                [record],
                db_path=db_path,
                summaries={
                    "receipt.jpg": {
                        "topic": "topic-a",
                        "meeting_place": "테스트식당",
                        "attendee_count": "3",
                        "attendee_names": "양대호;홍길동;김철수",
                    }
                },
            )

            conn = sqlite3.connect(db_path)
            try:
                receipt = conn.execute("SELECT file_name, status, file_sha256 FROM receipts").fetchone()
                self.assertEqual(receipt[0], "receipt.jpg")
                self.assertEqual(receipt[1], "generated")
                self.assertIsNotNone(receipt[2])
                document = conn.execute(
                    "SELECT kind, topic, meeting_place, attendee_count FROM generated_documents"
                ).fetchone()
                self.assertEqual(document, ("meeting", "topic-a", "테스트식당", 3))
                link_count = conn.execute("SELECT COUNT(*) FROM generated_document_receipts").fetchone()[0]
                self.assertEqual(link_count, 1)
            finally:
                conn.close()

    def test_email_delivery_links_generated_document(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt_path = root / "receipt.jpg"
            receipt_path.write_bytes(b"receipt")
            db_path = root / "meeting.sqlite3"
            record = ReceiptRecord(
                file_name="receipt.jpg",
                receipt_path=receipt_path,
                generated=datetime(2026, 6, 20, 12, 30),
                total_price=33000,
                store_name="테스트식당",
                address="서울시",
                receipt_type="restaurant",
                status="generated",
                document_type="topic-a",
                output_pdf="output/260620_1130_회의록.zip",
                ocr_engine="manual",
            )
            db.sync_records([record], db_path=db_path)
            delivery_id = db.mark_output_emailed(
                "meeting/output/260620_1130_회의록.zip",
                recipient="sheepvs5@gmail.com",
                subject="바이오나노연구원 법인카드 사용내역",
                gmail_message_id="gmail-id",
                db_path=db_path,
            )

            conn = sqlite3.connect(db_path)
            try:
                delivery = conn.execute(
                    "SELECT id, generated_document_id, output_path, gmail_message_id FROM email_deliveries"
                ).fetchone()
                self.assertEqual(delivery[0], delivery_id)
                self.assertIsNotNone(delivery[1])
                self.assertEqual(delivery[2], "output/260620_1130_회의록.zip")
                self.assertEqual(delivery[3], "gmail-id")
            finally:
                conn.close()

    def test_sync_records_replaces_stale_generated_document_link(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            receipt_path = root / "Image.jpg"
            receipt_path.write_bytes(b"receipt")
            db_path = root / "meeting.sqlite3"
            first = ReceiptRecord(
                file_name="Image.jpg",
                receipt_path=receipt_path,
                generated=datetime(2026, 2, 12, 15, 30),
                total_price=10000,
                receipt_type="restaurant",
                status="generated",
                document_type="topic-a",
                output_pdf="output/260212_1530_회의록.pdf",
            )
            second = ReceiptRecord(
                file_name="Image.jpg",
                receipt_path=receipt_path,
                generated=datetime(2026, 6, 3, 19, 0),
                total_price=12000,
                receipt_type="restaurant",
                status="generated",
                document_type="topic-b",
                output_pdf="output/260603_1900_회의록.zip",
            )

            db.sync_records([first], db_path=db_path)
            db.sync_records([second], db_path=db_path)

            conn = sqlite3.connect(db_path)
            try:
                links = conn.execute(
                    """
                    SELECT gd.output_pdf
                    FROM generated_document_receipts gdr
                    JOIN generated_documents gd ON gd.id=gdr.generated_document_id
                    """
                ).fetchall()
                self.assertEqual(links, [("output/260603_1900_회의록.zip",)])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
