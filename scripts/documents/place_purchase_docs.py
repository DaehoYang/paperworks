#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents.amounts import extract_financial_fields_from_pdf, ordered_price_similarity
from scripts.documents.check_tax_invoice_cases import best_matches, load_candidate_docs, match_score, parse_json_list
from scripts.documents.classifiers import missing_documents_for_doc_types, purchase_status_from_doc_types
from scripts.documents.db import (
    connect,
    replace_local_purchase_documents,
    source_key_from_metadata,
    upsert_document,
    upsert_processed_source,
    upsert_purchase_case,
    upsert_purchase_workflow,
)
from scripts.documents.ocr_metadata import enrich_metadata_from_pdf, structured_payload
from scripts.documents.purchase_scan import scan_purchase_root
from scripts.documents.vendors import canonical_vendor, normalize_vendor, parse_case_name, safe_name
from scripts.ocr.validation_profiles import validate_document


DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
DEFAULT_ARCHIVE = WORKSPACE_DIR / "purchase" / ".incoming"
DEFAULT_PURCHASE = WORKSPACE_DIR / "purchase"
DEFAULT_VENDOR_ROOT = DEFAULT_PURCHASE / "vendors"
DEFAULT_CARD_MIN_SCORE = 0.20
REQUIRED_FOR_PLACEMENT = ("estimate", "statement")
VENDOR_DOC_TYPES = ("business_registration", "bankbook_copy")
COPY_NAMES = {
    "tax_invoice": "전세.pdf",
    "estimate": "견적.pdf",
    "statement": "거명.pdf",
    "business_registration": "사업자등록증.pdf",
    "bankbook_copy": "통장사본.pdf",
    "receipt": "영수증.pdf",
}


@dataclass
class ExistingTax:
    path: Path
    case_dir: Path
    normalized_vendor: str
    amount: int | None
    item_prices: tuple[int, ...]


@dataclass
class PlacementPlan:
    status: str
    tax_doc: dict
    target_dir: Path
    docs: dict[str, dict]
    reason: str
    existing_path: Path | None = None


def doc_path(doc: dict) -> Path:
    return Path(doc.get("file_path") or doc.get("saved_pdf") or "")


def json_path(doc: dict) -> Path:
    return Path(doc.get("json_path") or "")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(path: Path, fallback: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return dict(fallback)


def save_document_metadata(doc: dict, pdf_path: Path, metadata_path: Path) -> dict:
    metadata = dict(doc)
    metadata["file_path"] = str(pdf_path)
    metadata["saved_pdf"] = str(pdf_path)
    metadata["json_path"] = str(metadata_path)
    metadata["source_key"] = metadata.get("source_key") or source_key_from_metadata(metadata)
    metadata["sha256"] = metadata.get("sha256") or file_sha256(pdf_path)
    metadata["status"] = metadata.get("status") or "active"
    metadata["saved_at"] = metadata.get("saved_at") or datetime.now(timezone.utc).isoformat()
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def install_document(
    doc: dict,
    target_pdf: Path,
    *,
    move_source: bool,
    db_path: Path | None = None,
) -> dict:
    source = doc_path(doc)
    if not source.exists():
        raise FileNotFoundError(source)
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target_pdf.resolve():
        if move_source:
            shutil.move(str(source), str(target_pdf))
        else:
            shutil.copy2(source, target_pdf)
    source_json = json_path(doc)
    metadata = load_metadata(source_json, doc)
    metadata.update(doc)
    metadata = save_document_metadata(metadata, target_pdf, target_pdf.with_suffix(".json"))
    if db_path:
        conn = connect(db_path)
        document_id = upsert_document(conn, metadata)
        upsert_processed_source(conn, metadata, document_id=document_id)
        conn.close()
    return metadata


def ensure_document_metadata(doc: dict, target_pdf: Path, db_path: Path | None = None) -> dict:
    if not target_pdf.exists():
        raise FileNotFoundError(target_pdf)
    metadata = load_metadata(target_pdf.with_suffix(".json"), doc)
    metadata.update(doc)
    metadata = save_document_metadata(metadata, target_pdf, target_pdf.with_suffix(".json"))
    if db_path:
        conn = connect(db_path)
        document_id = upsert_document(conn, metadata)
        upsert_processed_source(conn, metadata, document_id=document_id)
        conn.close()
    return metadata


def yymmdd(issue_date: str | None) -> str:
    if not issue_date:
        return "000000"
    return issue_date.replace("-", "")[2:8]


def target_dir_for_tax(tax_doc: dict, purchase_root: Path) -> Path:
    vendor = normalize_vendor(canonical_vendor(tax_doc.get("vendor"))) or safe_name(tax_doc.get("vendor") or "미상")
    return purchase_root / f"{yymmdd(tax_doc.get('issue_date'))}_{safe_name(vendor)}"


def case_status_from_doc_types(doc_types: set[str]) -> str:
    return purchase_status_from_doc_types(doc_types)


def local_doc_types(case_dir: Path) -> set[str]:
    if not case_dir.exists():
        return set()
    cases = scan_purchase_root(case_dir)
    if not cases:
        return set()
    return set(cases[0].local_docs)


def missing_doc_types(doc_types: set[str]) -> list[str]:
    return missing_documents_for_doc_types(doc_types)


def load_existing_tax_files(purchase_root: Path) -> list[ExistingTax]:
    existing: list[ExistingTax] = []
    for case in scan_purchase_root(purchase_root):
        parsed = parse_case_name(case.path)
        for paths in case.local_docs.get("tax_invoice", []):
            fields = extract_financial_fields_from_pdf(paths)
            existing.append(
                ExistingTax(
                    path=paths,
                    case_dir=case.path,
                    normalized_vendor=normalize_vendor(canonical_vendor(parsed.vendor or parsed.normalized_vendor)),
                    amount=fields.amount,
                    item_prices=fields.item_prices,
                )
            )
    return existing


def tax_already_in_purchase(tax_doc: dict, existing_tax_files: list[ExistingTax]) -> ExistingTax | None:
    vendor = normalize_vendor(canonical_vendor(tax_doc.get("vendor")))
    amount = tax_doc.get("amount")
    item_prices = tuple(int(value) for value in parse_json_list(tax_doc.get("item_prices") or tax_doc.get("item_prices_json")))
    for existing in existing_tax_files:
        if vendor and existing.normalized_vendor and vendor != existing.normalized_vendor:
            continue
        if amount and existing.amount and int(amount) == int(existing.amount):
            return existing
        if item_prices and existing.item_prices and ordered_price_similarity(item_prices, existing.item_prices) == 1.0:
            return existing
    return None


def choose_docs(tax_doc: dict, docs: list[dict], min_score: float, include_vendor_docs: bool) -> dict[str, dict]:
    matches = best_matches(tax_doc, docs, min_score)
    chosen: dict[str, dict] = {"tax_invoice": tax_doc}
    for doc_type in REQUIRED_FOR_PLACEMENT:
        match = matches.get(doc_type)
        if match:
            chosen[doc_type] = match[2]
    if include_vendor_docs:
        for doc_type in ("business_registration", "bankbook_copy"):
            match = matches.get(doc_type)
            if match:
                chosen[doc_type] = match[2]
    return chosen


def build_plans(
    *,
    docs: list[dict],
    purchase_root: Path,
    min_score: float,
    include_vendor_docs: bool,
) -> list[PlacementPlan]:
    existing_tax_files = load_existing_tax_files(purchase_root)
    plans: list[PlacementPlan] = []
    tax_docs = sorted(
        (doc for doc in docs if doc.get("doc_type") == "tax_invoice"),
        key=lambda doc: (doc.get("issue_date") or "", normalize_vendor(doc.get("vendor")), doc.get("amount") or 0),
    )
    for tax_doc in tax_docs:
        target_dir = target_dir_for_tax(tax_doc, purchase_root)
        existing = tax_already_in_purchase(tax_doc, existing_tax_files)
        if existing:
            target_dir = existing.case_dir
        chosen = choose_docs(tax_doc, docs, min_score, include_vendor_docs)
        anticipated_types = local_doc_types(target_dir) | set(chosen)
        status = case_status_from_doc_types(anticipated_types)
        missing = missing_doc_types(anticipated_types)
        reason = "ready" if not missing else "missing " + ",".join(missing)
        plans.append(PlacementPlan(status, tax_doc, target_dir, chosen, reason, existing.path if existing else None))
    return plans


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 100):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find available target directory for {path}")


def target_for_plan(plan: PlacementPlan) -> Path:
    if plan.target_dir.exists():
        return plan.target_dir
    return unique_target(plan.target_dir)


def should_install_doc(target_dir: Path, doc_type: str) -> bool:
    standard_target = target_dir / COPY_NAMES[doc_type]
    if standard_target.exists():
        return False
    return doc_type not in local_doc_types(target_dir)


def source_should_move(source: Path, target_dir: Path, move_sources: bool, doc_type: str) -> bool:
    if not move_sources or doc_type in VENDOR_DOC_TYPES:
        return False
    try:
        resolved_source = source.resolve()
        resolved_target = target_dir.resolve()
    except OSError:
        return False
    if resolved_target == resolved_source or resolved_target in resolved_source.parents:
        return False
    if any(parent.name == "purchase" for parent in resolved_source.parents):
        return False
    return True


def vendor_dir(vendor_root: Path, vendor: str | None) -> Path:
    canonical = canonical_vendor(vendor) or vendor
    normalized = normalize_vendor(canonical) or safe_name(canonical or "미상")
    return vendor_root / safe_name(normalized)


def load_vendor_docs(vendor_root: Path) -> list[dict]:
    docs: list[dict] = []
    if not vendor_root.exists():
        return docs
    for metadata_path in sorted(vendor_root.rglob("*.json")):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        data.setdefault("json_path", str(metadata_path))
        data.setdefault("file_path", str(metadata_path.with_suffix(".pdf")))
        docs.append(data)
    return docs


def vendor_docs_by_vendor(vendor_root: Path) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for doc in load_vendor_docs(vendor_root):
        vendor = normalize_vendor(canonical_vendor(doc.get("vendor") or doc.get("normalized_vendor")))
        doc_type = doc.get("doc_type")
        if not vendor or doc_type not in VENDOR_DOC_TYPES:
            continue
        result.setdefault(vendor, {}).setdefault(doc_type, doc)
    return result


def copy_vendor_docs_to_purchase(
    *,
    purchase_root: Path,
    vendor_root: Path,
) -> list[Path]:
    copied: list[Path] = []
    vendor_docs = vendor_docs_by_vendor(vendor_root)
    if not vendor_docs:
        return copied
    for case in scan_purchase_root(purchase_root):
        case_vendor = normalize_vendor(canonical_vendor(case.vendor or case.normalized_vendor))
        docs_for_vendor = vendor_docs.get(case_vendor)
        if not docs_for_vendor:
            continue
        existing_doc_types = set(case.local_docs)
        for doc_type in VENDOR_DOC_TYPES:
            if doc_type in existing_doc_types:
                continue
            doc = docs_for_vendor.get(doc_type)
            if not doc:
                continue
            target = case.path / COPY_NAMES[doc_type]
            if target.exists():
                continue
            metadata = validated_vendor_doc_metadata(doc_path(doc), doc_type, case.vendor, base_metadata=doc)
            if not metadata:
                continue
            install_document(metadata, target, move_source=False, db_path=None)
            copied.append(target)
    return copied


def card_case_baseline(case) -> dict:
    canonical = canonical_vendor(case.vendor or case.normalized_vendor)
    return {
        "doc_type": "receipt",
        "vendor": canonical or case.vendor,
        "normalized_vendor": normalize_vendor(canonical or case.normalized_vendor),
        "issue_date": case.case_date,
        "document_number": case.document_number,
        "item_code": case.item_code,
    }


def choose_card_payment_docs(case, docs: list[dict], min_score: float) -> dict[str, dict]:
    baseline = card_case_baseline(case)
    chosen: dict[str, dict] = {}
    for doc_type in REQUIRED_FOR_PLACEMENT:
        if doc_type in case.local_docs:
            continue
        ranked: list[tuple[float, list[str], dict]] = []
        for doc in docs:
            if doc.get("doc_type") in {"tax_invoice", "receipt", *VENDOR_DOC_TYPES}:
                continue
            score, reasons = match_score(baseline, doc, doc_type)
            if score >= min_score:
                ranked.append((score, reasons, doc))
        ranked.sort(key=lambda row: row[0], reverse=True)
        if ranked:
            chosen[doc_type] = ranked[0][2]
    return chosen


def sync_purchase_case_from_local(case_dir: Path, db_path: Path) -> None:
    scanned = scan_purchase_root(case_dir)
    if not scanned:
        return
    case = scanned[0]
    conn = connect(db_path)
    try:
        case_db = case.as_db_dict()
        case_db["status"] = case_status_from_doc_types(set(case.local_docs))
        case_id = upsert_purchase_case(conn, case_db)
        replace_local_purchase_documents(conn, case_id, case.local_docs)
    finally:
        conn.close()


def local_purchase_case_id(case_dir: Path, db_path: Path) -> int | None:
    scanned = scan_purchase_root(case_dir)
    if not scanned:
        return None
    case = scanned[0]
    conn = connect(db_path)
    try:
        case_db = case.as_db_dict()
        case_db["status"] = case_status_from_doc_types(set(case.local_docs))
        case_id = upsert_purchase_case(conn, case_db)
        replace_local_purchase_documents(conn, case_id, case.local_docs)
        return case_id
    finally:
        conn.close()


def items_current(case_dir: Path, quote_path: Path) -> bool:
    items_path = case_dir / "items.xls"
    return items_path.exists() and items_path.stat().st_mtime >= quote_path.stat().st_mtime


def record_items_workflow(db_path: Path, case_id: int, **updates: object) -> None:
    conn = connect(db_path)
    try:
        upsert_purchase_workflow(conn, case_id, updates)
    finally:
        conn.close()


def prepare_purchase_items(case_dir: Path, db_path: Path) -> str:
    from scripts.paperwork.purchase.process_purchase import (
        DEFAULT_LITELLM_BASE_URL,
        DEFAULT_OCR_API_URL,
        find_quote_file,
        prepare_items_xls,
    )

    case_id = local_purchase_case_id(case_dir, db_path)
    if case_id is None:
        return "skipped:no_case"
    try:
        quote_path = find_quote_file(case_dir)
    except FileNotFoundError:
        record_items_workflow(
            db_path,
            case_id,
            items_status="pending",
            items_generated_at=None,
            items_error="missing_estimate",
        )
        return "pending:missing_estimate"

    if items_current(case_dir, quote_path):
        record_items_workflow(
            db_path,
            case_id,
            items_status="generated",
            items_generated_at=datetime.fromtimestamp((case_dir / "items.xls").stat().st_mtime, timezone.utc).isoformat(),
            items_error=None,
        )
        return "generated:current"

    try:
        prepare_items_xls(
            quote_pdf=quote_path,
            items_path=case_dir / "items.xls",
            parse_engine="auto",
            ocr_api_url=os.environ.get("DHLAB_OCR_API_URL", DEFAULT_OCR_API_URL),
            ocr_api_key=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", ""),
            litellm_base_url=os.environ.get("DHLAB_LITELLM_BASE_URL", DEFAULT_LITELLM_BASE_URL),
            litellm_api_key=os.environ.get("DHLAB_LITELLM_API_KEY", ""),
            litellm_model=os.environ.get("DHLAB_LITELLM_MODEL", "local"),
            codex_bin="codex",
            codex_model=None,
            timeout=180,
        )
    except Exception as exc:
        record_items_workflow(
            db_path,
            case_id,
            items_status="failed",
            items_generated_at=None,
            items_error=str(exc),
        )
        return "failed"

    record_items_workflow(
        db_path,
        case_id,
        items_status="generated",
        items_generated_at=datetime.now(timezone.utc).isoformat(),
        items_error=None,
    )
    return "generated"


def prepare_purchase_items_for_cases(purchase_root: Path, db_path: Path) -> dict[str, str]:
    results: dict[str, str] = {}
    for case in scan_purchase_root(purchase_root):
        if not case.path.name[:1].isdigit():
            continue
        if "tax_invoice" not in case.local_docs:
            continue
        results[str(case.path)] = prepare_purchase_items(case.path, db_path)
    return results


def fill_existing_card_payment_cases(
    *,
    docs: list[dict],
    purchase_root: Path,
    db_path: Path | None = None,
    min_score: float = DEFAULT_CARD_MIN_SCORE,
) -> list[Path]:
    placed: list[Path] = []
    for case in scan_purchase_root(purchase_root):
        if "receipt" not in case.local_docs or "tax_invoice" in case.local_docs:
            continue
        chosen = choose_card_payment_docs(case, docs, min_score)
        for doc_type, doc in chosen.items():
            if not should_install_doc(case.path, doc_type):
                continue
            target = case.path / COPY_NAMES[doc_type]
            install_document(doc, target, move_source=False, db_path=db_path)
            placed.append(target)
        if db_path and chosen:
            sync_purchase_case_from_local(case.path, db_path)
    return placed


def vendor_metadata(source: Path, doc_type: str, vendor: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    canonical = canonical_vendor(vendor) or vendor
    return {
        "doc_type": doc_type,
        "all_doc_types": [doc_type],
        "vendor": canonical,
        "original_vendor": vendor if canonical != vendor else None,
        "normalized_vendor": normalize_vendor(canonical),
        "issue_date": None,
        "amount": None,
        "currency": "KRW",
        "confidence": 0.7,
        "source": "local",
        "source_type": "local_purchase",
        "source_filename": source.name,
        "source_path": str(source),
        "saved_pdf": str(source),
        "file_path": str(source),
        "json_path": str(source.with_suffix(".json")),
        "sha256": file_sha256(source),
        "status": "active",
        "saved_at": now,
    }


def validated_vendor_doc_metadata(
    source: Path,
    doc_type: str,
    vendor: str | None,
    *,
    base_metadata: dict | None = None,
) -> dict | None:
    if doc_type not in VENDOR_DOC_TYPES or not source.exists():
        return None
    metadata = load_metadata(source.with_suffix(".json"), base_metadata or vendor_metadata(source, doc_type, vendor or "미상"))
    metadata.update(base_metadata or {})
    metadata["doc_type"] = doc_type
    metadata["all_doc_types"] = [doc_type]
    metadata.setdefault("vendor", vendor)
    enriched = enrich_metadata_from_pdf(metadata, source)
    expected_vendor = canonical_vendor(vendor or enriched.get("vendor")) or vendor
    validation = validate_document(doc_type, structured_payload(enriched), expected_vendor=expected_vendor)
    if not validation.ok:
        print(
            f"skip-vendor-doc: {source} doc_type={doc_type} "
            f"missing={','.join(validation.missing_fields) or '-'} "
            f"invalid={','.join(validation.invalid_fields) or '-'}"
        )
        return None
    enriched["ocr_validation"] = {
        "ok": validation.ok,
        "missing_fields": list(validation.missing_fields),
        "invalid_fields": list(validation.invalid_fields),
        "reason": validation.reason,
    }
    enriched.setdefault("ocr_status", "validated_cached")
    return enriched


def refresh_vendor_store_from_purchase(
    *,
    purchase_root: Path,
    vendor_root: Path,
    db_path: Path | None = None,
) -> list[dict]:
    installed: list[dict] = []
    for case in scan_purchase_root(purchase_root):
        if not case.vendor:
            continue
        for doc_type in VENDOR_DOC_TYPES:
            for source in case.local_docs.get(doc_type, []):
                target = vendor_dir(vendor_root, case.vendor) / COPY_NAMES[doc_type]
                if target.exists():
                    continue
                metadata = validated_vendor_doc_metadata(source, doc_type, case.vendor)
                if not metadata:
                    continue
                metadata = install_document(
                    metadata,
                    target,
                    move_source=False,
                    db_path=db_path,
                )
                installed.append(metadata)
    return installed


def install_collected_vendor_docs(
    docs: list[dict],
    *,
    vendor_root: Path,
    db_path: Path | None = None,
) -> list[dict]:
    installed: list[dict] = []
    for doc in docs:
        doc_type = doc.get("doc_type")
        if doc_type not in VENDOR_DOC_TYPES:
            continue
        target = vendor_dir(vendor_root, doc.get("vendor")) / COPY_NAMES[doc_type]
        if target.exists():
            continue
        metadata = validated_vendor_doc_metadata(doc_path(doc), doc_type, doc.get("vendor"), base_metadata=doc)
        if not metadata:
            continue
        installed.append(install_document(metadata, target, move_source=True, db_path=db_path))
    return installed


def apply_plan(plan: PlacementPlan, db_path: Path | None, *, move_sources: bool = False) -> Path:
    target_dir = target_for_plan(plan)
    target_dir.mkdir(parents=True, exist_ok=True)
    for doc_type, doc in plan.docs.items():
        if not should_install_doc(target_dir, doc_type):
            continue
        source = doc_path(doc)
        install_document(
            doc,
            target_dir / COPY_NAMES[doc_type],
            move_source=source_should_move(source, target_dir, move_sources, doc_type),
            db_path=db_path,
        )

    if db_path:
        final_types = local_doc_types(target_dir)
        status = case_status_from_doc_types(final_types)
        conn = connect(db_path)
        upsert_purchase_case(
            conn,
            {
                "case_dir": str(target_dir),
                "case_name": target_dir.name,
                "case_date": plan.tax_doc.get("issue_date"),
                "vendor": canonical_vendor(plan.tax_doc.get("vendor")) or plan.tax_doc.get("vendor"),
                "normalized_vendor": normalize_vendor(canonical_vendor(plan.tax_doc.get("vendor")) or plan.tax_doc.get("vendor")),
                "document_number": plan.tax_doc.get("document_number"),
                "item_code": plan.tax_doc.get("item_code"),
                "amount": plan.tax_doc.get("amount"),
                "status": status,
            },
        )
        conn.close()
    return target_dir


def sync_db_status(plans: list[PlacementPlan], db_path: Path) -> None:
    conn = connect(db_path)
    for plan in plans:
        status = {
            "ready": "ready",
            "finished": "finished",
            "incomplete": "incomplete",
        }.get(plan.status, plan.status)
        upsert_purchase_case(
            conn,
            {
                "case_dir": str(plan.target_dir),
                "case_name": plan.target_dir.name,
                "case_date": plan.tax_doc.get("issue_date"),
                "vendor": canonical_vendor(plan.tax_doc.get("vendor")) or plan.tax_doc.get("vendor"),
                "normalized_vendor": normalize_vendor(canonical_vendor(plan.tax_doc.get("vendor")) or plan.tax_doc.get("vendor")),
                "document_number": plan.tax_doc.get("document_number"),
                "item_code": plan.tax_doc.get("item_code"),
                "amount": plan.tax_doc.get("amount"),
                "status": status,
            },
        )
    conn.close()


def print_plans(plans: list[PlacementPlan]) -> None:
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.status] = counts.get(plan.status, 0) + 1
    print(" ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    for plan in plans:
        tax = plan.tax_doc
        print(
            f"{plan.status}: {tax.get('issue_date')} {tax.get('vendor')} "
            f"amount={tax.get('amount') or '-'} target={plan.target_dir}"
        )
        if plan.existing_path:
            print(f"  existing={plan.existing_path}")
        if plan.reason != "ready":
            print(f"  reason={plan.reason}")
        for doc_type, doc in plan.docs.items():
            print(f"  {doc_type}: {doc_path(doc)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Place collected Gmail documents into purchase folders.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--purchase-root", type=Path, default=DEFAULT_PURCHASE)
    parser.add_argument("--vendor-root", type=Path, default=DEFAULT_VENDOR_ROOT)
    parser.add_argument("--min-score", type=float, default=0.50)
    parser.add_argument("--card-min-score", type=float, default=DEFAULT_CARD_MIN_SCORE)
    parser.add_argument("--include-vendor-docs", action="store_true")
    parser.add_argument("--refresh-vendor-store", action="store_true")
    parser.add_argument("--no-card-fill", action="store_true", help="Do not fill existing card-payment folders.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--move-sources", action="store_true")
    parser.add_argument("--sync-db", action="store_true", help="Update purchase_cases statuses for all tax-invoice cases.")
    parser.add_argument("--no-db", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = None if args.no_db else args.db
    if args.refresh_vendor_store:
        refresh_vendor_store_from_purchase(
            purchase_root=args.purchase_root,
            vendor_root=args.vendor_root,
            db_path=db_path,
        )
    docs = load_candidate_docs(args.db, args.archive)
    docs.extend(load_vendor_docs(args.vendor_root))
    copy_vendor_docs_to_purchase(purchase_root=args.purchase_root, vendor_root=args.vendor_root)
    if not args.no_card_fill:
        card_placed = fill_existing_card_payment_cases(
            docs=docs,
            purchase_root=args.purchase_root,
            db_path=db_path,
            min_score=args.card_min_score,
        )
        for path in card_placed:
            print(f"card-placed: {path}")
    plans = build_plans(
        docs=docs,
        purchase_root=args.purchase_root,
        min_score=args.min_score,
        include_vendor_docs=args.include_vendor_docs,
    )
    print_plans(plans)
    if args.sync_db and db_path:
        sync_db_status(plans, db_path)
    if args.apply:
        for plan in plans:
            target = apply_plan(plan, db_path, move_sources=args.move_sources)
            print(f"placed: {target}")
    if db_path and (args.apply or args.sync_db):
        for case_dir, result in sorted(prepare_purchase_items_for_cases(args.purchase_root, db_path).items()):
            print(f"items-prepare: {result} {case_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
