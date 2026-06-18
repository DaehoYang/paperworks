#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents.classifiers import DOC_TYPE_LABELS, REQUIRED_DOCUMENTS
from scripts.documents.db import connect, load_documents, load_json_archive
from scripts.documents.purchase_scan import scan_purchase_root
from scripts.documents.vendors import normalize_vendor


DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
DEFAULT_ARCHIVES = [WORKSPACE_DIR / "purchase" / ".incoming"]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def date_score(left: str | None, right: str | None) -> float:
    a = parse_date(left)
    b = parse_date(right)
    if not a or not b:
        return 0.0
    delta = abs((a - b).days)
    if delta == 0:
        return 0.20
    if delta <= 7:
        return 0.15
    if delta <= 30:
        return 0.08
    return 0.0


def doc_all_types(doc: dict) -> set[str]:
    raw = doc.get("all_doc_types")
    if isinstance(raw, list):
        return {str(x) for x in raw}
    raw_json = doc.get("all_doc_types_json")
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                return {str(x) for x in parsed}
        except json.JSONDecodeError:
            pass
    return {str(doc.get("doc_type") or "unknown")}


def score_doc(case, doc: dict, needed_type: str) -> tuple[float, list[str]]:
    all_types = doc_all_types(doc)
    if needed_type not in all_types and doc.get("doc_type") != needed_type:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    doc_vendor = doc.get("vendor")
    if case.normalized_vendor and case.normalized_vendor == normalize_vendor(doc_vendor):
        score += 0.50
        reasons.append("vendor")
    ds = date_score(case.case_date, doc.get("issue_date") or doc.get("email_date"))
    if ds:
        score += ds
        reasons.append("date")
    if case.document_number and case.document_number == doc.get("document_number"):
        score += 0.25
        reasons.append("document_number")
    if case.item_code and case.item_code == doc.get("item_code"):
        score += 0.20
        reasons.append("item_code")
    return min(score, 1.0), reasons


def load_candidate_docs(db_path: Path, archives: list[Path]) -> list[dict]:
    docs: list[dict] = []
    if db_path.exists():
        conn = connect(db_path)
        docs.extend(dict(row) for row in load_documents(conn))
    seen = {(doc.get("sha256"), doc.get("doc_type")) for doc in docs}
    for item in load_json_archive(archives):
        key = (item.get("sha256") or item.get("source_sha256"), item.get("doc_type"))
        if key not in seen:
            docs.append(item)
            seen.add(key)
    return docs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Suggest archived documents for purchase folders.")
    parser.add_argument("purchase_path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--min-score", type=float, default=0.45)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archives = args.archive or DEFAULT_ARCHIVES
    docs = load_candidate_docs(args.db, archives)
    cases = scan_purchase_root(args.purchase_path)
    for case in cases:
        print(case.path)
        missing = [doc_type for doc_type in REQUIRED_DOCUMENTS if doc_type not in case.local_docs]
        if not missing:
            print("  missing: none")
            continue
        for needed in missing:
            ranked = []
            for doc in docs:
                score, reasons = score_doc(case, doc, needed)
                if score >= args.min_score:
                    ranked.append((score, reasons, doc))
            ranked.sort(key=lambda row: row[0], reverse=True)
            print(f"  {DOC_TYPE_LABELS[needed]}:")
            if not ranked:
                print("    no candidates")
                continue
            for score, reasons, doc in ranked[:5]:
                path = doc.get("file_path") or doc.get("saved_pdf")
                print(
                    f"    score={score:.2f} reasons={','.join(reasons)} "
                    f"vendor={doc.get('vendor') or '-'} date={doc.get('issue_date') or '-'} path={path}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
