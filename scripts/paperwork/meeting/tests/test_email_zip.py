from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.paperwork.meeting import email_zip


class EmailZipTests(unittest.TestCase):
    def test_build_message_attaches_zip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "260528_1830_회의록.zip"
            path.write_bytes(b"zip-content")
            message = email_zip.build_message(
                to="sheepvs5@gmail.com",
                subject="바이오나노연구원 법인카드 사용내역",
                body="첨부드립니다.",
                zip_path=path,
            )

        self.assertEqual(message["To"], "sheepvs5@gmail.com")
        self.assertEqual(message["Subject"], "바이오나노연구원 법인카드 사용내역")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "260528_1830_회의록.zip")
        self.assertEqual(attachments[0].get_content_type(), "application/zip")

    def test_build_message_rejects_non_zip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "회의록.pdf"
            path.write_bytes(b"pdf")
            with self.assertRaises(ValueError):
                email_zip.build_message(
                    to="sheepvs5@gmail.com",
                    subject="subject",
                    body="body",
                    zip_path=path,
                )


if __name__ == "__main__":
    unittest.main()
