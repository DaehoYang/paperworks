#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents import collect_tax_invoices as tax
from scripts.documents.amounts import extract_financial_fields_from_pdf, extract_pdf_text
from scripts.documents.classifiers import (
    classify_document,
    classify_document_content,
    document_types_from_filename,
    extract_issue_date_from_document_text,
    extract_vendor,
    extract_vendor_from_document_text,
    is_probably_admin_notice,
)
from scripts.documents.db import (
    connect,
    processed_source_has_existing_document,
    processed_source_row,
    source_key,
    upsert_document,
)
from scripts.documents.vendors import canonical_vendor, normalize_vendor, safe_name


DEFAULT_OUTPUT_DIR = WORKSPACE_DIR / "purchase" / ".incoming"
DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
DEFAULT_CREDENTIALS = WORKSPACE_DIR / "credentials.json"
DEFAULT_TOKEN = Path(__file__).resolve().parent / "token.json"

PROCESSED_LABEL = tax.PROCESSED_LABEL
UNPROCESSED_LABEL = tax.UNPROCESSED_LABEL

DOCUMENT_QUERIES = [
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} from:hometaxadmin@hometax.go.kr',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} "NTS_eTaxInvoice.html"',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} "전자세금계산서"',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} "전자(세금)계산서"',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} has:attachment filename:pdf (견적서 OR 견적 OR quotation OR quote)',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} has:attachment filename:pdf (거래명세서 OR "거래 명세서" OR 거명)',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} has:attachment (사업자등록증 OR "사업자 등록증")',
    'in:anywhere -in:sent -in:spam -in:trash newer_than:{newer_than} has:attachment (통장사본 OR "통장 사본" OR 계좌사본 OR "계좌 사본")',
]

IMAGE_EXTENSIONS = tax.IMAGE_ATTACHMENT_EXTS
SUPPORTED_EXTENSIONS = {".pdf", ".xml", ".html", ".htm", *IMAGE_EXTENSIONS}


def query_set(newer_than: str, include_admin_mail: bool) -> list[str]:
    queries = [query.format(newer_than=newer_than) for query in DOCUMENT_QUERIES]
    if not include_admin_mail:
        queries = [query + " -from:phys@gachon.ac.kr" for query in queries]
    return queries


def output_stem(issue_date: str, vendor: str, doc_type: str, message_id: str, index: int | None = None) -> str:
    yymmdd = issue_date.replace("-", "")[2:]
    stem = f"{yymmdd}_{safe_name(vendor)}_{doc_type}_{message_id}"
    if index and index > 1:
        stem += f"_{index}"
    return stem


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def convert_attachment_to_pdf(source_path: Path, target_path: Path, hometax_password: str | None) -> None:
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise RuntimeError(f"unsupported attachment type: {source_path.name}")
    tax.convert_to_pdf(source_path, target_path, hometax_password=hometax_password)


def build_metadata(
    *,
    message: dict,
    message_id: str,
    from_: str,
    subject: str,
    email_dt: datetime,
    issue_date: str,
    vendor: str,
    classification,
    pdf_path: Path,
    metadata_path: Path,
    pdf_sha: str,
    source_type: str,
    source_attachment_id: str | None = None,
    source_filename: str | None = None,
    source_mime_type: str | None = None,
    source_size: int | None = None,
    source_sha256: str | None = None,
    source_link: str | None = None,
) -> dict:
    financial_fields = extract_financial_fields_from_pdf(pdf_path)
    canonical = canonical_vendor(vendor) or vendor
    return {
        "doc_type": classification.doc_type,
        "all_doc_types": list(classification.all_doc_types or (classification.doc_type,)),
        "vendor": canonical,
        "original_vendor": vendor if canonical != vendor else None,
        "normalized_vendor": normalize_vendor(canonical),
        "document_number": classification.document_number,
        "item_code": classification.item_code,
        "issue_date": issue_date,
        "amount": financial_fields.amount,
        "item_count": financial_fields.item_count,
        "item_prices": list(financial_fields.item_prices),
        "currency": "KRW",
        "confidence": classification.confidence,
        "message_id": message_id,
        "thread_id": message.get("threadId"),
        "from": from_,
        "subject": subject,
        "email_date": email_dt.isoformat(),
        "gmail_url": f"https://mail.google.com/mail/#all/{message_id}",
        "source": "gmail",
        "source_type": source_type,
        "source_attachment_id": source_attachment_id,
        "source_filename": source_filename,
        "source_mime_type": source_mime_type,
        "source_size": source_size,
        "source_sha256": source_sha256,
        "source_link": source_link,
        "source_key": source_key(
            gmail_message_id=message_id,
            source_type=source_type,
            source_attachment_id=source_attachment_id,
            source_filename=source_filename,
            source_size=source_size,
            source_link=source_link,
        ),
        "saved_pdf": str(pdf_path),
        "json_path": str(metadata_path),
        "sha256": pdf_sha,
        "status": "active",
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def save_metadata(metadata_path: Path, metadata: dict) -> None:
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def metadata_from_pdf(
    *,
    temp_pdf_path: Path,
    output_dir: Path,
    message: dict,
    message_id: str,
    from_: str,
    subject: str,
    email_dt: datetime,
    fallback_issue_date: str,
    fallback_vendor: str,
    fallback_classification,
    source_type: str,
    source_attachment_id: str | None = None,
    source_filename: str | None = None,
    source_mime_type: str | None = None,
    source_size: int | None = None,
    source_sha256: str | None = None,
    source_link: str | None = None,
    index: int | None = None,
) -> tuple[Path, Path, dict]:
    pdf_text = extract_pdf_text(temp_pdf_path)
    classification = classify_document_content(source_filename or temp_pdf_path.name, pdf_text, fallback_classification)
    issue_date = extract_issue_date_from_document_text(pdf_text, fallback_issue_date) or fallback_issue_date
    vendor = extract_vendor_from_document_text(pdf_text, classification.doc_type) or fallback_vendor
    stem = output_stem(issue_date, vendor, classification.doc_type, message_id, index)
    pdf_path = output_dir / f"{stem}.pdf"
    metadata_path = output_dir / f"{stem}.json"
    metadata = build_metadata(
        message=message,
        message_id=message_id,
        from_=from_,
        subject=subject,
        email_dt=email_dt,
        issue_date=issue_date,
        vendor=vendor,
        classification=classification,
        pdf_path=pdf_path,
        metadata_path=metadata_path,
        pdf_sha=sha256_bytes(temp_pdf_path.read_bytes()),
        source_type=source_type,
        source_attachment_id=source_attachment_id,
        source_filename=source_filename,
        source_mime_type=source_mime_type,
        source_size=source_size,
        source_sha256=source_sha256,
        source_link=source_link,
    )
    return pdf_path, metadata_path, metadata


def collect(args: argparse.Namespace) -> int:
    service = tax.gmail_service(args.credentials, args.token)
    conn = None if args.no_db or args.dry_run else connect(args.db)
    source_conn = None
    if not args.dry_run and not getattr(args, "no_source_tracking", False):
        source_conn = connect(args.db)
    labels = {}
    managed_label_ids = set()
    if not args.dry_run and not args.no_labels:
        existing_managed = tax.existing_label_ids(service, tax.MANAGED_LABELS)
        labels = {
            "ok": tax.get_or_create_label(service, PROCESSED_LABEL),
            "unprocessed": tax.get_or_create_label(service, UNPROCESSED_LABEL),
        }
        managed_label_ids = set(existing_managed.values()) | set(labels.values())

    message_ids = tax.search_message_ids(
        service,
        query_set(args.newer_than, args.include_admin_mail),
        max_results=args.max_messages,
    )
    print(f"Found {len(message_ids)} candidate messages")

    ok_count = 0
    manual_count = 0
    error_count = 0
    for message_id in message_ids:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        subject = tax.header(headers, "Subject")
        from_ = tax.header(headers, "From")
        if is_probably_admin_notice(from_) and not args.include_admin_mail:
            continue
        date_value = tax.header(headers, "Date")
        email_dt = tax.parsed_email_datetime(date_value) if date_value else datetime.now().astimezone()
        body_text = tax.body_text_from_payload(payload)
        body_html = tax.body_html_from_payload(payload)
        issue_date = tax.infer_issue_date(body_text, email_dt)
        message_vendor = extract_vendor(subject, body_text, from_) or tax.infer_vendor(subject, body_text)

        attachments = tax.attachments_from_message(message)
        saved_in_message = 0
        message_had_error = False

        for index, part in enumerate(attachments, start=1):
            suffix = Path(part.filename).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                continue
            classification = classify_document(part.filename, subject, body_text, from_)
            if classification.doc_type == "unknown":
                continue
            if suffix in IMAGE_EXTENSIONS and classification.doc_type not in document_types_from_filename(part.filename):
                continue
            if suffix in {".html", ".htm", ".xml"} and classification.doc_type != "tax_invoice":
                continue
            source_key_value = source_key(
                gmail_message_id=message_id,
                source_type="attachment",
                source_attachment_id=part.attachment_id,
                source_filename=part.filename,
                source_size=part.size,
            )
            if source_conn and not args.force:
                processed = processed_source_row(
                    source_conn,
                    source_key_value=source_key_value,
                    gmail_message_id=message_id,
                    source_type="attachment",
                    source_attachment_id=part.attachment_id,
                    source_filename=part.filename,
                    source_size=part.size,
                )
                if processed_source_has_existing_document(processed):
                    print(f"skip: processed {part.filename}")
                    saved_in_message += 1
                    continue

            if args.dry_run:
                stem = output_stem(issue_date, message_vendor, classification.doc_type, message_id, index)
                print(f"dry-run: {classification.doc_type} {part.filename} -> {args.output_dir / f'{stem}.pdf'}")
                saved_in_message += 1
                continue

            try:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                data = tax.decode_attachment(service, message_id, part.attachment_id)
                source_sha = sha256_bytes(data)
                with tempfile.TemporaryDirectory() as td:
                    source_path = Path(td) / part.filename
                    temp_pdf_path = Path(td) / "converted.pdf"
                    source_path.write_bytes(data)
                    convert_attachment_to_pdf(source_path, temp_pdf_path, args.hometax_password)
                    pdf_path, metadata_path, metadata = metadata_from_pdf(
                        temp_pdf_path=temp_pdf_path,
                        output_dir=args.output_dir,
                        message=message,
                        message_id=message_id,
                        from_=from_,
                        subject=subject,
                        email_dt=email_dt,
                        fallback_issue_date=issue_date,
                        fallback_vendor=message_vendor,
                        fallback_classification=classification,
                        source_type="attachment",
                        source_attachment_id=part.attachment_id,
                        source_filename=part.filename,
                        source_mime_type=part.mime_type,
                        source_size=part.size,
                        source_sha256=source_sha,
                        index=index,
                    )
                    if pdf_path.exists() and metadata_path.exists() and not args.force:
                        saved_in_message += 1
                        continue
                    shutil.move(str(temp_pdf_path), str(pdf_path))
                save_metadata(metadata_path, metadata)
                if conn:
                    upsert_document(conn, metadata)
                ok_count += 1
                saved_in_message += 1
                print(f"ok: {pdf_path}")
            except Exception as exc:
                error_count += 1
                message_had_error = True
                print(f"error: {message_id} {part.filename}: {exc}")

        if saved_in_message == 0:
            links = tax.allowed_invoice_links(body_html, body_text)
            for index, url in enumerate(links, start=1):
                classification = classify_document("NTS_eTaxInvoice.html", subject, body_text, from_)
                source_key_value = source_key(
                    gmail_message_id=message_id,
                    source_type="link",
                    source_link=url,
                )
                if source_conn and not args.force:
                    processed = processed_source_row(
                        source_conn,
                        source_key_value=source_key_value,
                        gmail_message_id=message_id,
                        source_type="link",
                        source_link=url,
                    )
                    if processed_source_has_existing_document(processed):
                        print(f"skip: processed link {url}")
                        saved_in_message += 1
                        continue
                if args.dry_run:
                    stem = output_stem(issue_date, message_vendor, "tax_invoice", message_id, index)
                    pdf_path = args.output_dir / f"{stem}.pdf"
                    print(f"dry-run: link tax_invoice {url} -> {pdf_path}")
                    saved_in_message += 1
                    continue
                try:
                    args.output_dir.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as td:
                        temp_pdf_path = Path(td) / "linked.pdf"
                        tax.link_to_pdf(url, temp_pdf_path)
                        pdf_path, metadata_path, metadata = metadata_from_pdf(
                            temp_pdf_path=temp_pdf_path,
                            output_dir=args.output_dir,
                            message=message,
                            message_id=message_id,
                            from_=from_,
                            subject=subject,
                            email_dt=email_dt,
                            fallback_issue_date=issue_date,
                            fallback_vendor=message_vendor,
                            fallback_classification=classification,
                            source_type="link",
                            source_link=url,
                            index=index,
                        )
                        if pdf_path.exists() and metadata_path.exists() and not args.force:
                            saved_in_message += 1
                            continue
                        shutil.move(str(temp_pdf_path), str(pdf_path))
                    save_metadata(metadata_path, metadata)
                    if conn:
                        upsert_document(conn, metadata)
                    ok_count += 1
                    saved_in_message += 1
                    print(f"ok: {pdf_path}")
                except Exception as exc:
                    error_count += 1
                    message_had_error = True
                    print(f"error: {message_id} link {url}: {exc}")

        if labels:
            if message_had_error:
                tax.set_managed_label(service, message_id, labels["unprocessed"], managed_label_ids)
            elif saved_in_message:
                tax.set_managed_label(service, message_id, labels["ok"], managed_label_ids)
            else:
                manual_count += 1
                tax.set_managed_label(service, message_id, labels["unprocessed"], managed_label_ids)
        elif saved_in_message == 0:
            manual_count += 1

    if conn:
        conn.close()
    if source_conn:
        source_conn.close()
    print(f"Done: ok={ok_count}, manual={manual_count}, errors={error_count}")
    return 1 if error_count else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Gmail purchase documents as PDFs and index them.")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--newer-than", default="1d", help="Gmail newer_than value, e.g. 1d, 14d, 2m.")
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--force", action="store_true", help="Overwrite existing PDF and JSON files.")
    parser.add_argument("--dry-run", action="store_true", help="Search and classify without writing files or labels.")
    parser.add_argument("--no-db", action="store_true", help="Do not write SQLite index.")
    parser.add_argument("--no-source-tracking", action="store_true", help="Do not skip already processed Gmail sources.")
    parser.add_argument("--no-labels", action="store_true", help="Do not apply Gmail labels.")
    parser.add_argument("--include-admin-mail", action="store_true", help="Include school/admin announcement senders.")
    parser.add_argument(
        "--hometax-password",
        default=os.environ.get("HOMETAX_PASSWORD"),
        help="HomeTax secure mail password, usually recipient business number. Defaults to HOMETAX_PASSWORD.",
    )
    return parser.parse_args()


def main() -> int:
    return collect(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
