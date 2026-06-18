#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents.classifiers import (
    DOC_TYPE_LABELS,
    missing_documents_for_doc_types,
    purchase_status_from_doc_types,
    required_documents_for_doc_types,
)
from scripts.documents.db import connect, replace_local_purchase_documents, upsert_purchase_case
from scripts.documents.place_purchase_docs import DEFAULT_VENDOR_ROOT, copy_vendor_docs_to_purchase
from scripts.documents.purchase_scan import PurchaseCase, scan_purchase_root


DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
def purchase_status(present_types: set[str]) -> str:
    return purchase_status_from_doc_types(present_types)


def case_status(case: PurchaseCase) -> dict:
    present = {doc_type: [str(path) for path in paths] for doc_type, paths in case.local_docs.items()}
    present_types = set(present)
    required = list(required_documents_for_doc_types(present_types))
    missing = missing_documents_for_doc_types(present_types)
    return {
        "case_dir": str(case.path),
        "case_date": case.case_date,
        "vendor": case.vendor,
        "normalized_vendor": case.normalized_vendor,
        "document_number": case.document_number,
        "item_code": case.item_code,
        "present": present,
        "required": required,
        "missing": missing,
        "status": purchase_status(present_types),
    }


def render_text(rows: list[dict]) -> str:
    chunks: list[str] = []
    for row in rows:
        chunks.append(row["case_dir"])
        chunks.append(f"status: {row.get('status')}")
        chunks.append(f"vendor: {row.get('vendor') or 'unknown'}")
        if row.get("document_number") or row.get("item_code"):
            chunks.append(f"codes: {row.get('document_number') or '-'} / {row.get('item_code') or '-'}")
        chunks.append("")
        chunks.append("OK")
        for doc_type in row["required"]:
            paths = row["present"].get(doc_type) or []
            if not paths:
                continue
            label = DOC_TYPE_LABELS[doc_type]
            for path in paths:
                chunks.append(f"- {label}: {path}")
        chunks.append("")
        if row["missing"]:
            chunks.append("MISSING")
            for doc_type in row["missing"]:
                chunks.append(f"- {DOC_TYPE_LABELS[doc_type]}")
        else:
            chunks.append("missing: none")
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n"


def render_markdown(rows: list[dict]) -> str:
    chunks = ["# Purchase Document Checklist", ""]
    for row in rows:
        chunks.append(f"## `{row['case_dir']}`")
        chunks.append("")
        chunks.append(f"- vendor: `{row.get('vendor') or 'unknown'}`")
        chunks.append(f"- date: `{row.get('case_date') or 'unknown'}`")
        chunks.append(f"- status: `{row.get('status')}`")
        if row.get("document_number") or row.get("item_code"):
            chunks.append(f"- codes: `{row.get('document_number') or '-'}` / `{row.get('item_code') or '-'}`")
        chunks.append("")
        chunks.append("| document | status | path |")
        chunks.append("| --- | --- | --- |")
        for doc_type in row["required"]:
            paths = row["present"].get(doc_type) or []
            if paths:
                chunks.append(f"| {DOC_TYPE_LABELS[doc_type]} | OK | `{paths[0]}` |")
            else:
                chunks.append(f"| {DOC_TYPE_LABELS[doc_type]} | MISSING |  |")
        chunks.append("")
    return "\n".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check required purchase documents in local purchase folders.")
    parser.add_argument("purchase_path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--vendor-root", type=Path, default=DEFAULT_VENDOR_ROOT)
    parser.add_argument("--no-vendor-copy", action="store_true", help="Do not copy reusable vendor documents into purchase folders.")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--no-db", action="store_true", help="Only scan files; do not update SQLite.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.no_vendor_copy:
        copy_vendor_docs_to_purchase(purchase_root=args.purchase_path, vendor_root=args.vendor_root)
    cases = scan_purchase_root(args.purchase_path)
    if not args.no_db:
        conn = connect(args.db)
        for case in cases:
            case_db = case.as_db_dict()
            case_db["status"] = purchase_status(set(case.local_docs))
            case_id = upsert_purchase_case(conn, case_db)
            replace_local_purchase_documents(conn, case_id, case.local_docs)
    rows = [case_status(case) for case in cases]
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(render_markdown(rows))
    else:
        print(render_text(rows), end="")
    return 1 if any(row["missing"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
