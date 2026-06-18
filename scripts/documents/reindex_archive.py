#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents.amounts import extract_financial_fields_from_pdf, extract_pdf_text
from scripts.documents.classifiers import (
    Classification,
    classify_document_content,
    extract_issue_date_from_document_text,
    extract_vendor_from_document_text,
)
from scripts.documents.db import connect, upsert_document
from scripts.documents.vendors import normalize_vendor


DEFAULT_ARCHIVE = WORKSPACE_DIR / "purchase" / ".incoming"
DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"


def reindex_archive(archive: Path, db_path: Path | None) -> tuple[int, int]:
    conn = connect(db_path) if db_path else None
    updated = 0
    skipped = 0
    for json_path in sorted(archive.glob("*.json")):
        try:
            metadata = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            skipped += 1
            continue
        pdf_path = Path(metadata.get("saved_pdf") or metadata.get("file_path") or json_path.with_suffix(".pdf"))
        if not pdf_path.exists():
            skipped += 1
            continue
        previous_doc_type = metadata.get("doc_type")
        text = extract_pdf_text(pdf_path)
        fallback = Classification(
            metadata.get("doc_type") or "unknown",
            tuple(metadata.get("all_doc_types") or [metadata.get("doc_type") or "unknown"]),
            float(metadata.get("confidence") or 0),
            "existing metadata",
            metadata.get("document_number"),
            metadata.get("item_code"),
        )
        classification = classify_document_content(metadata.get("source_filename") or pdf_path.name, text, fallback)
        vendor = extract_vendor_from_document_text(text, classification.doc_type) or metadata.get("vendor")
        issue_date = extract_issue_date_from_document_text(text, metadata.get("issue_date")) or metadata.get("issue_date")
        fields = extract_financial_fields_from_pdf(pdf_path)
        metadata["doc_type"] = classification.doc_type
        metadata["all_doc_types"] = list(classification.all_doc_types or (classification.doc_type,))
        metadata["vendor"] = vendor
        metadata["normalized_vendor"] = normalize_vendor(vendor)
        metadata["document_number"] = classification.document_number
        metadata["item_code"] = classification.item_code
        metadata["issue_date"] = issue_date
        metadata["confidence"] = classification.confidence
        metadata["amount"] = fields.amount
        metadata["item_count"] = fields.item_count
        metadata["item_prices"] = list(fields.item_prices)
        metadata["json_path"] = str(json_path)
        metadata["saved_pdf"] = str(pdf_path)
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if conn:
            if previous_doc_type and previous_doc_type != classification.doc_type and metadata.get("sha256"):
                conn.execute(
                    "UPDATE documents SET status='reclassified', updated_at=datetime('now') WHERE sha256=? AND doc_type<>?",
                    (metadata["sha256"], classification.doc_type),
                )
                conn.commit()
            upsert_document(conn, metadata)
        updated += 1
    if conn:
        conn.close()
    return updated, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild amount and item-price metadata for archived PDFs.")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--no-db", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    updated, skipped = reindex_archive(args.archive, None if args.no_db else args.db)
    print(f"updated={updated} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
