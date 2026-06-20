#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents import collect_documents
from scripts.documents import collect_tax_invoices as tax
from scripts.documents.check_tax_invoice_cases import load_candidate_docs
from scripts.documents.db import load_json_archive
from scripts.documents.place_purchase_docs import (
    DEFAULT_DB,
    DEFAULT_PURCHASE,
    DEFAULT_VENDOR_ROOT,
    DEFAULT_CARD_MIN_SCORE,
    apply_plan,
    build_plans,
    case_status_from_doc_types,
    copy_vendor_docs_to_purchase,
    fill_existing_card_payment_cases,
    install_collected_vendor_docs,
    load_vendor_docs,
    local_doc_types,
    print_plans,
    refresh_vendor_store_from_purchase,
    sync_db_status,
)


DEFAULT_CREDENTIALS = WORKSPACE_DIR / "credentials.json"
DEFAULT_TOKEN = Path(__file__).resolve().parent / "token.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Gmail purchase documents and place complete cases into purchase folders."
    )
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--purchase-root", type=Path, default=DEFAULT_PURCHASE)
    parser.add_argument("--vendor-root", type=Path, default=DEFAULT_VENDOR_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--newer-than", default="90d", help="Gmail newer_than value, e.g. 1d, 90d, 2m.")
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--min-score", type=float, default=0.50)
    parser.add_argument(
        "--card-min-score",
        type=float,
        default=DEFAULT_CARD_MIN_SCORE,
        help="Minimum score for filling existing card-payment folders from collected estimate/statement docs.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument("--include-admin-mail", action="store_true")
    parser.add_argument(
        "--hometax-password",
        default=os.environ.get("HOMETAX_PASSWORD"),
        help="HomeTax secure mail password, usually recipient business number. Defaults to HOMETAX_PASSWORD.",
    )
    return parser.parse_args()


def collect_to_incoming(args: argparse.Namespace, output_dir: Path) -> int:
    collect_args = SimpleNamespace(
        credentials=args.credentials,
        token=args.token,
        output_dir=output_dir,
        db=args.db,
        newer_than=args.newer_than,
        max_messages=args.max_messages,
        force=False,
        dry_run=args.dry_run,
        no_db=True,
        no_labels=args.no_labels,
        include_admin_mail=args.include_admin_mail,
        hometax_password=args.hometax_password,
        no_source_tracking=False,
    )
    return collect_documents.collect(collect_args)


def message_ids_from_case(case_dir: Path) -> set[str]:
    message_ids: set[str] = set()
    for metadata_path in sorted(case_dir.glob("*.json")) if case_dir.exists() else []:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        message_id = metadata.get("message_id") or metadata.get("gmail_message_id")
        if message_id:
            message_ids.add(str(message_id))
    return message_ids


def mark_finished_case_messages(args: argparse.Namespace, case_dir: Path) -> None:
    if case_status_from_doc_types(local_doc_types(case_dir)) != "finished":
        return
    message_ids = message_ids_from_case(case_dir)
    if not message_ids:
        return
    service = tax.gmail_service(args.credentials, args.token)
    existing_managed = tax.existing_label_ids(service, tax.MANAGED_LABELS)
    finished_id = tax.get_or_create_label(service, tax.FINISHED_LABEL)
    managed_label_ids = set(existing_managed.values()) | {finished_id}
    for message_id in sorted(message_ids):
        tax.set_managed_label(service, message_id, finished_id, managed_label_ids)
        print(f"finished-label: {message_id}")


def main() -> int:
    args = parse_args()
    db_path = None if args.dry_run else args.db

    if not args.dry_run:
        refresh_vendor_store_from_purchase(
            purchase_root=args.purchase_root,
            vendor_root=args.vendor_root,
            db_path=db_path,
        )

    incoming_dir = args.purchase_root / ".incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    collect_status = collect_to_incoming(args, incoming_dir)
    if args.dry_run:
        return collect_status

    collected_docs = load_json_archive([incoming_dir])
    install_collected_vendor_docs(
        collected_docs,
        vendor_root=args.vendor_root,
        db_path=db_path,
    )
    copy_vendor_docs_to_purchase(purchase_root=args.purchase_root, vendor_root=args.vendor_root)
    candidate_docs = load_candidate_docs(args.db, incoming_dir)
    case_docs = [
        doc
        for doc in candidate_docs
        if doc.get("doc_type") not in {"business_registration", "bankbook_copy"}
    ]
    card_placed = fill_existing_card_payment_cases(
        docs=case_docs,
        purchase_root=args.purchase_root,
        db_path=db_path,
        min_score=args.card_min_score,
    )
    for path in card_placed:
        print(f"card-placed: {path}")
    candidate_docs = case_docs + load_vendor_docs(args.vendor_root)

    plans = build_plans(
        docs=candidate_docs,
        purchase_root=args.purchase_root,
        min_score=args.min_score,
        include_vendor_docs=True,
    )
    print_plans(plans)
    if db_path:
        sync_db_status(plans, db_path)
    for plan in plans:
        target = apply_plan(plan, db_path, move_sources=True)
        print(f"placed: {target}")
        if not args.no_labels:
            mark_finished_case_messages(args, target)

    return collect_status


if __name__ == "__main__":
    raise SystemExit(main())
