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

from scripts.documents.amounts import ordered_price_similarity
from scripts.documents.classifiers import DOC_TYPE_LABELS, document_types_from_filename
from scripts.documents.db import connect, load_documents, load_json_archive
from scripts.documents.vendors import normalize_vendor


DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
DEFAULT_ARCHIVE = WORKSPACE_DIR / "purchase" / ".incoming"
REQUIRED_OTHER_DOCS = ("estimate", "statement", "business_registration", "bankbook_copy")


def parse_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def date_score(left: str | None, right: str | None) -> tuple[float, str | None]:
    a = parse_date(left)
    b = parse_date(right)
    if not a or not b:
        return 0.0, None
    delta = abs((a - b).days)
    if delta == 0:
        return 0.08, "same_date"
    if delta <= 7:
        return 0.05, "near_date"
    if delta <= 30:
        return 0.02, "month_date"
    return 0.0, None


def doc_types(doc: dict) -> set[str]:
    primary = str(doc.get("doc_type") or "unknown")
    filename_types = set(document_types_from_filename(doc.get("source_filename") or ""))
    if filename_types:
        filename_types.add(primary)
        return filename_types
    return {primary}


def amount_score(tax_doc: dict, doc: dict) -> tuple[float, list[str]]:
    reasons: list[str] = []
    tax_amount = tax_doc.get("amount")
    doc_amount = doc.get("amount")
    score = 0.0
    if tax_amount and doc_amount:
        delta = abs(int(tax_amount) - int(doc_amount))
        if delta == 0:
            score += 0.50
            reasons.append("amount")
        elif delta / max(int(tax_amount), int(doc_amount)) <= 0.01:
            score += 0.25
            reasons.append("amount_1pct")

    tax_count = tax_doc.get("item_count")
    doc_count = doc.get("item_count")
    if tax_count and doc_count and int(tax_count) == int(doc_count):
        score += 0.15
        reasons.append("item_count")

    tax_prices = [int(value) for value in parse_json_list(tax_doc.get("item_prices") or tax_doc.get("item_prices_json"))]
    doc_prices = [int(value) for value in parse_json_list(doc.get("item_prices") or doc.get("item_prices_json"))]
    price_similarity = ordered_price_similarity(tax_prices, doc_prices)
    if price_similarity == 1.0:
        score += 0.25
        reasons.append("item_prices")
    elif price_similarity:
        score += 0.15 * price_similarity
        reasons.append("partial_item_prices")

    return score, reasons


def match_score(tax_doc: dict, doc: dict, needed_type: str) -> tuple[float, list[str]]:
    if needed_type not in doc_types(doc) and doc.get("doc_type") != needed_type:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []

    tax_vendor = normalize_vendor(tax_doc.get("vendor"))
    doc_vendor = normalize_vendor(doc.get("vendor"))
    if tax_vendor and doc_vendor and tax_vendor != doc_vendor:
        return 0.0, []
    if tax_vendor and tax_vendor == doc_vendor:
        if needed_type in {"business_registration", "bankbook_copy"}:
            score += 0.70
        else:
            score += 0.15
        reasons.append("vendor")

    if needed_type in {"estimate", "statement"}:
        amount_points, amount_reasons = amount_score(tax_doc, doc)
        score += amount_points
        reasons.extend(amount_reasons)

    tax_code_values = {tax_doc.get("document_number"), tax_doc.get("item_code")} - {None, ""}
    doc_code_values = {doc.get("document_number"), doc.get("item_code")} - {None, ""}
    if tax_code_values and doc_code_values and tax_code_values & doc_code_values:
        score += 0.10
        reasons.append("code")

    date_points, date_reason = date_score(tax_doc.get("issue_date"), doc.get("issue_date") or doc.get("email_date"))
    if date_points:
        score += date_points
        if date_reason:
            reasons.append(date_reason)

    return min(score, 1.0), reasons


def load_candidate_docs(db_path: Path, archive: Path) -> list[dict]:
    docs: list[dict] = []
    if db_path.exists():
        conn = connect(db_path)
        docs.extend(dict(row) for row in load_documents(conn))
    seen = {(doc.get("sha256"), doc.get("doc_type")) for doc in docs}
    for doc in load_json_archive([archive]):
        key = (doc.get("sha256") or doc.get("source_sha256"), doc.get("doc_type"))
        if key not in seen:
            docs.append(doc)
            seen.add(key)
    return docs


def best_matches(tax_doc: dict, docs: list[dict], min_score: float) -> dict[str, tuple[float, list[str], dict] | None]:
    matches: dict[str, tuple[float, list[str], dict] | None] = {}
    for needed_type in REQUIRED_OTHER_DOCS:
        ranked = []
        for doc in docs:
            if doc is tax_doc or doc.get("sha256") == tax_doc.get("sha256"):
                continue
            score, reasons = match_score(tax_doc, doc, needed_type)
            if score >= min_score:
                ranked.append((score, reasons, doc))
        ranked.sort(key=lambda row: row[0], reverse=True)
        matches[needed_type] = ranked[0] if ranked else None
    return matches


def summarize(docs: list[dict], min_score: float) -> list[tuple[str, dict, dict[str, tuple[float, list[str], dict] | None]]]:
    rows = []
    for tax_doc in sorted((doc for doc in docs if doc.get("doc_type") == "tax_invoice"), key=lambda doc: (doc.get("issue_date") or "", doc.get("vendor") or "", doc.get("source_filename") or "")):
        matches = best_matches(tax_doc, docs, min_score)
        missing = [doc_type for doc_type, match in matches.items() if not match]
        if not missing:
            status = "complete"
        elif len(missing) == len(REQUIRED_OTHER_DOCS):
            status = "empty"
        else:
            status = "partial"
        rows.append((status, tax_doc, matches))
    return rows


def print_text(rows) -> None:
    counts = {"complete": 0, "partial": 0, "empty": 0}
    for status, _, _ in rows:
        counts[status] += 1
    print(f"complete={counts['complete']} partial={counts['partial']} empty={counts['empty']} total={len(rows)}")
    for status, tax_doc, matches in rows:
        label = f"{tax_doc.get('issue_date') or '-'} {tax_doc.get('vendor') or '-'}"
        source = tax_doc.get("source_filename") or Path(tax_doc.get("file_path") or tax_doc.get("saved_pdf") or "").name
        amount = tax_doc.get("amount") or "-"
        print(f"\n[{status}] {label} amount={amount} source={source}")
        for doc_type in REQUIRED_OTHER_DOCS:
            match = matches[doc_type]
            if not match:
                print(f"  MISSING {DOC_TYPE_LABELS[doc_type]}")
                continue
            score, reasons, doc = match
            print(
                f"  OK {DOC_TYPE_LABELS[doc_type]} score={score:.2f} "
                f"reasons={','.join(reasons)} amount={doc.get('amount') or '-'} "
                f"file={doc.get('source_filename') or Path(doc.get('file_path') or doc.get('saved_pdf') or '').name}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check collected documents using tax invoices as the case baseline.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--min-score", type=float, default=0.50)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = summarize(load_candidate_docs(args.db, args.archive), args.min_score)
    if args.format == "json":
        payload = []
        for status, tax_doc, matches in rows:
            payload.append(
                {
                    "status": status,
                    "tax_invoice": tax_doc,
                    "matches": {
                        doc_type: None
                        if not match
                        else {"score": match[0], "reasons": match[1], "document": match[2]}
                        for doc_type, match in matches.items()
                    },
                }
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print_text(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
