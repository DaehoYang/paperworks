from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = Path(__file__).resolve().parents[3]
BASE_DIR = WORKSPACE_DIR / "meeting"
ASSETS_DIR = PACKAGE_DIR / "assets"
RECEIPT_DIR = BASE_DIR / "receipt"
USED_RECEIPT_DIR = RECEIPT_DIR / "used"
OUTPUT_DIR = BASE_DIR / "output"
MEETING_DB = BASE_DIR / "meeting.sqlite3"

INFO_YML = ASSETS_DIR / "information.yml"
MINUTES_TEMPLATE_PDF = ASSETS_DIR / "바나연회의록_빈칸.pdf"
MINUTES_FORM_PDF = ASSETS_DIR / "바나연회의록_입력가능.pdf"
TRIP_SOURCE_PDF = ASSETS_DIR / "출장보고서.pdf"
TRIP_TEMPLATE_PDF = ASSETS_DIR / "출장보고서_입력가능.pdf"

RECORDS_CSV = RECEIPT_DIR / "records.csv"
SUMMARY_CSV = RECEIPT_DIR / "summary.csv"
OCR_TEXT_DIR = RECEIPT_DIR / "ocr_text"
