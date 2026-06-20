#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
    record_processed_document,
    source_key,
)
from scripts.documents.ocr_metadata import enrich_metadata_from_pdf
from scripts.documents.ocr_metadata import structured_payload
from scripts.documents.vendors import canonical_vendor, normalize_vendor, safe_name
from scripts.ocr.validation_profiles import validate_document


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
SPLITTABLE_DOC_TYPES = {"tax_invoice", "estimate", "statement", "business_registration", "bankbook_copy"}
VENDOR_DOC_TYPES = {"business_registration", "bankbook_copy"}
CONTINUABLE_DOC_TYPES = {"tax_invoice", "estimate", "statement"}
COMBINED_FILENAME_RE = re.compile(
    r"(?:\+|_|,|，|및|와|과|증빙|서류|일체|통합|합본|첨부)",
    re.IGNORECASE,
)


@dataclass
class PageAnalysis:
    page_number: int
    page_pdf: Path
    text: str
    explicit_doc_types: list[str]
    candidate_doc_types: list[str]
    validated_doc_type: str | None = None
    metadata: dict | None = None


@dataclass
class PageSegment:
    doc_type: str
    pages: list[PageAnalysis]


def is_hometax_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "hometax.go.kr" or host.endswith(".hometax.go.kr")


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
    content_pdf_path: Path,
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
    financial_fields = extract_financial_fields_from_pdf(content_pdf_path)
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


def pdf_page_count(pdf_path: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    return 1


def split_pdf_pages(pdf_path: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "page-%d.pdf"
    subprocess.run(["pdfseparate", str(pdf_path), str(pattern)], check=True, capture_output=True)
    return [output_dir / f"page-{index}.pdf" for index in range(1, pdf_page_count(pdf_path) + 1)]


def unite_pdf_pages(page_pdfs: list[Path], target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if len(page_pdfs) == 1:
        shutil.copy2(page_pdfs[0], target_path)
        return target_path
    subprocess.run(["pdfunite", *(str(path) for path in page_pdfs), str(target_path)], check=True, capture_output=True)
    return target_path


def compact_document_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def has_tax_invoice_text_signals(text: str) -> bool:
    compact = compact_document_text(text)
    return bool(re.search(r"전자.{0,20}계산서", compact)) and "합계금액" in compact


def document_start_types(text: str) -> set[str]:
    compact = compact_document_text(text)
    starts: set[str] = set()
    if re.search(r"전자.{0,20}계산서", compact):
        starts.add("tax_invoice")
    if "견적서" in compact or "quotation" in text.lower():
        starts.add("estimate")
    if "거래명세서" in compact or "거래명세표" in compact:
        starts.add("statement")
    if "사업자등록증" in compact:
        starts.add("business_registration")
    if any(token in compact for token in ("통장사본", "계좌사본")):
        starts.add("bankbook_copy")
    return starts


def has_combined_source_hint(metadata: dict) -> bool:
    values = [
        metadata.get("source_filename"),
        metadata.get("subject"),
    ]
    return any(COMBINED_FILENAME_RE.search(str(value or "")) for value in values)


def validate_collected_document(doc_type: str, metadata: dict, pdf_text: str) -> tuple[bool, list[str], list[str], str]:
    payload = structured_payload(metadata)
    validation = validate_document(doc_type, payload)
    missing = list(validation.missing_fields)
    invalid = list(validation.invalid_fields)
    reasons = [validation.reason] if validation.reason else []

    if doc_type == "tax_invoice":
        if not metadata.get("vendor"):
            missing.append("vendor")
            reasons.append("missing vendor")
        if not has_tax_invoice_text_signals(pdf_text):
            invalid.append("tax_invoice_text")
            reasons.append("missing electronic invoice text or total label")

    missing = sorted(set(missing))
    invalid = sorted(set(invalid))
    return not missing and not invalid, missing, invalid, "; ".join(reason for reason in reasons if reason)


def set_validation_metadata(metadata: dict, ok: bool, missing: list[str], invalid: list[str], reason: str) -> None:
    metadata["ocr_validation"] = {
        "ok": ok,
        "missing_fields": missing,
        "invalid_fields": invalid,
        "reason": reason,
    }


def should_try_page_split(metadata: dict, pdf_path: Path) -> bool:
    try:
        pages = pdf_page_count(pdf_path)
    except Exception:
        return False
    if pages < 2:
        return False
    doc_types = set(metadata.get("all_doc_types") or [metadata.get("doc_type")])
    doc_types = {doc_type for doc_type in doc_types if doc_type in SPLITTABLE_DOC_TYPES}
    if len(doc_types) >= 2:
        return True
    validation = metadata.get("ocr_validation") or {}
    if doc_types & {"tax_invoice", *VENDOR_DOC_TYPES} and validation.get("ok") is False:
        return True
    try:
        text = extract_pdf_text(pdf_path)
    except Exception:
        text = ""
    if len(document_start_types(text)) >= 2:
        return True
    return has_combined_source_hint(metadata)


def page_range_label(pages: list[PageAnalysis]) -> str:
    start = pages[0].page_number
    end = pages[-1].page_number
    return f"p{start}" if start == end else f"p{start}-{end}"


def segment_source_key(base_metadata: dict, doc_type: str, page_label: str, is_first: bool) -> str | None:
    base_key = base_metadata.get("source_key") or source_key(
        gmail_message_id=base_metadata.get("message_id"),
        source_type=base_metadata.get("source_type"),
        source_attachment_id=base_metadata.get("source_attachment_id"),
        source_filename=base_metadata.get("source_filename"),
        source_size=base_metadata.get("source_size"),
        source_link=base_metadata.get("source_link"),
    )
    if not base_key:
        return None
    if is_first:
        return base_key
    return f"{base_key}:derived:{doc_type}:{page_label}"


def clear_segment_fields(metadata: dict) -> None:
    for key in (
        "amount",
        "item_count",
        "item_prices",
        "issue_date",
        "document_number",
        "item_code",
        "bank_name",
        "account_holder",
        "account_number",
        "business_registration_number",
        "ocr_status",
        "ocr_method",
        "ocr_validation",
        "ocr_attempts",
        "ocr_codex_attempted",
        "ocr_codex_validated",
    ):
        metadata.pop(key, None)


def explicit_doc_types_from_page_text(page_pdf: Path, text: str) -> list[str]:
    classification = classify_document_content(page_pdf.name, text, None)
    return [doc_type for doc_type in classification.all_doc_types if doc_type in SPLITTABLE_DOC_TYPES]


def segment_candidate_types(base_metadata: dict, page_pdf: Path, text: str | None = None) -> list[str]:
    page_text = extract_pdf_text(page_pdf) if text is None else text
    explicit_types = explicit_doc_types_from_page_text(page_pdf, page_text)
    candidates: list[str] = []
    for doc_type in [*explicit_types, *(base_metadata.get("all_doc_types") or [])]:
        if doc_type in SPLITTABLE_DOC_TYPES and doc_type not in candidates:
            candidates.append(doc_type)
    return candidates


def validated_segment_metadata(
    *,
    base_metadata: dict,
    segment_pdf: Path,
    output_dir: Path,
    doc_type: str,
    pages: list[PageAnalysis],
    is_first_segment: bool,
) -> dict | None:
    metadata = dict(base_metadata)
    page_label = page_range_label(pages)
    clear_segment_fields(metadata)
    metadata["doc_type"] = doc_type
    metadata["all_doc_types"] = [doc_type]
    metadata["source_page_start"] = pages[0].page_number
    metadata["source_page_end"] = pages[-1].page_number
    metadata["source_original_pdf"] = str(base_metadata.get("source_original_pdf") or base_metadata.get("saved_pdf") or "")
    metadata["source_key"] = segment_source_key(base_metadata, doc_type, page_label, is_first_segment)
    metadata["source_filename"] = base_metadata.get("source_filename")
    metadata["sha256"] = sha256_bytes(segment_pdf.read_bytes())

    enriched = enrich_metadata_from_pdf(metadata, segment_pdf)
    expected_vendor = None
    if doc_type in VENDOR_DOC_TYPES:
        expected_vendor = canonical_vendor(base_metadata.get("vendor") or enriched.get("vendor")) or base_metadata.get("vendor")
    segment_text = extract_pdf_text(segment_pdf)
    if doc_type in VENDOR_DOC_TYPES:
        validation = validate_document(doc_type, structured_payload(enriched), expected_vendor=expected_vendor)
        ok = validation.ok
        missing = list(validation.missing_fields)
        invalid = list(validation.invalid_fields)
        reason = validation.reason
    else:
        ok, missing, invalid, reason = validate_collected_document(doc_type, enriched, segment_text)
    if not ok:
        return None

    set_validation_metadata(enriched, ok, missing, invalid, reason)
    issue_date = enriched.get("issue_date") or base_metadata.get("issue_date") or datetime.now().date().isoformat()
    vendor = base_metadata.get("vendor") or enriched.get("vendor") or "미상"
    canonical = canonical_vendor(vendor) or vendor
    enriched["vendor"] = canonical
    enriched["normalized_vendor"] = normalize_vendor(canonical)
    stem = f"{output_stem(issue_date, canonical, doc_type, base_metadata.get('message_id') or 'local')}_{page_label}"
    pdf_path = output_dir / f"{stem}.pdf"
    metadata_path = output_dir / f"{stem}.json"
    enriched["saved_pdf"] = str(pdf_path)
    enriched["file_path"] = str(pdf_path)
    enriched["json_path"] = str(metadata_path)
    enriched["saved_at"] = datetime.now(timezone.utc).isoformat()
    return enriched


def has_new_document_start(text: str, current_doc_type: str) -> bool:
    return bool(document_start_types(text) - {current_doc_type})


def has_purchase_continuation_clues(text: str) -> bool:
    compact = "".join(text.split())
    if not compact:
        return False
    tokens = (
        "품명",
        "품목",
        "규격",
        "수량",
        "단가",
        "금액",
        "공급가액",
        "부가세",
        "합계",
        "total",
        "amount",
        "price",
    )
    if any(token.lower() in compact.lower() for token in tokens):
        return True
    return bool(re.search(r"\d{1,3}(?:,\d{3})+", text))


def has_tax_invoice_continuation_clues(text: str) -> bool:
    compact = compact_document_text(text)
    if not compact:
        return False
    tokens = ("합계금액", "공급가액", "승인번호", "세액", "수정사유")
    if re.search(r"전자.{0,20}계산서", compact):
        return True
    if any(token in compact for token in tokens):
        return True
    return bool(re.search(r"\d{1,3}(?:,\d{3})+", text))


def is_continuation_page(segment: PageSegment, page: PageAnalysis) -> bool:
    if segment.doc_type not in CONTINUABLE_DOC_TYPES:
        return False
    if page.validated_doc_type:
        return page.validated_doc_type == segment.doc_type
    if page.explicit_doc_types and segment.doc_type not in page.explicit_doc_types:
        return False
    if has_new_document_start(page.text, segment.doc_type):
        return False
    if segment.doc_type == "tax_invoice":
        return has_tax_invoice_continuation_clues(page.text)
    return segment.doc_type in page.candidate_doc_types and has_purchase_continuation_clues(page.text)


def analyze_page(base_metadata: dict, page_number: int, page_pdf: Path) -> PageAnalysis:
    text = extract_pdf_text(page_pdf)
    explicit_doc_types = explicit_doc_types_from_page_text(page_pdf, text)
    candidate_doc_types = segment_candidate_types(base_metadata, page_pdf, text)
    analysis = PageAnalysis(
        page_number=page_number,
        page_pdf=page_pdf,
        text=text,
        explicit_doc_types=explicit_doc_types,
        candidate_doc_types=candidate_doc_types,
    )
    for doc_type in candidate_doc_types:
        metadata = validated_segment_metadata(
            base_metadata=base_metadata,
            segment_pdf=page_pdf,
            output_dir=page_pdf.parent,
            doc_type=doc_type,
            pages=[analysis],
            is_first_segment=False,
        )
        if metadata:
            analysis.validated_doc_type = doc_type
            analysis.metadata = metadata
            break
    return analysis


def build_page_segments(pages: list[PageAnalysis]) -> list[PageSegment]:
    segments: list[PageSegment] = []
    current: PageSegment | None = None
    for page in pages:
        if page.validated_doc_type:
            if current and current.doc_type == page.validated_doc_type:
                current.pages.append(page)
            else:
                current = PageSegment(page.validated_doc_type, [page])
                segments.append(current)
            continue
        if current and is_continuation_page(current, page):
            current.pages.append(page)
            continue
        current = None
    return segments


def page_split_outputs(
    *,
    temp_pdf_path: Path,
    output_dir: Path,
    base_metadata: dict,
    work_dir: Path,
) -> list[tuple[Path, Path, Path, dict]]:
    if not should_try_page_split(base_metadata, temp_pdf_path):
        return []

    outputs: list[tuple[Path, Path, Path, dict]] = []
    pages = split_pdf_pages(temp_pdf_path, work_dir / "pages")
    analyses = [analyze_page(base_metadata, page_number, page_pdf) for page_number, page_pdf in enumerate(pages, start=1)]
    segments = build_page_segments(analyses)
    used_types: set[str] = set()
    for segment in segments:
        if segment.doc_type in used_types and segment.doc_type in VENDOR_DOC_TYPES:
            continue
        segment_pdf = unite_pdf_pages(
            [page.page_pdf for page in segment.pages],
            work_dir / "segments" / f"{segment.doc_type}-{page_range_label(segment.pages)}.pdf",
        )
        metadata = validated_segment_metadata(
            base_metadata=base_metadata,
            segment_pdf=segment_pdf,
            output_dir=output_dir,
            doc_type=segment.doc_type,
            pages=segment.pages,
            is_first_segment=len(outputs) == 0,
        )
        if not metadata:
            continue
        outputs.append((segment_pdf, Path(metadata["saved_pdf"]), Path(metadata["json_path"]), metadata))
        used_types.add(segment.doc_type)
    return outputs


def preserve_original_pdf(temp_pdf_path: Path, output_dir: Path, metadata: dict, index: int | None) -> Path:
    originals_dir = output_dir / "originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    source_stem = safe_name(Path(metadata.get("source_filename") or "source").stem)
    message_id = metadata.get("message_id") or "local"
    suffix = f"_{index}" if index else ""
    target = originals_dir / f"{message_id}{suffix}_{source_stem}.pdf"
    if not target.exists():
        shutil.copy2(temp_pdf_path, target)
    return target


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
        content_pdf_path=temp_pdf_path,
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
    metadata = enrich_metadata_from_pdf(metadata, temp_pdf_path)
    ok, missing, invalid, reason = validate_collected_document(metadata.get("doc_type") or "unknown", metadata, pdf_text)
    set_validation_metadata(metadata, ok, missing, invalid, reason)
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
                    outputs = page_split_outputs(
                        temp_pdf_path=temp_pdf_path,
                        output_dir=args.output_dir,
                        base_metadata=metadata,
                        work_dir=Path(td),
                    )
                    if outputs:
                        original_path = preserve_original_pdf(temp_pdf_path, args.output_dir, metadata, index)
                        for _, _, _, segment_metadata in outputs:
                            segment_metadata["source_original_pdf"] = str(original_path)
                        print(f"split: {part.filename} -> {len(outputs)} docs")
                    else:
                        outputs = [(temp_pdf_path, pdf_path, metadata_path, metadata)]

                    for source_pdf, target_pdf, target_json, output_metadata in outputs:
                        if target_pdf.exists() and target_json.exists() and not args.force:
                            record_processed_document(conn, source_conn, output_metadata)
                            saved_in_message += 1
                            continue
                        if source_pdf.resolve() == temp_pdf_path.resolve() and len(outputs) == 1:
                            shutil.move(str(source_pdf), str(target_pdf))
                        else:
                            shutil.copy2(source_pdf, target_pdf)
                        save_metadata(target_json, output_metadata)
                        record_processed_document(conn, source_conn, output_metadata)
                        ok_count += 1
                        saved_in_message += 1
                        print(f"ok: {target_pdf}")
            except Exception as exc:
                error_count += 1
                message_had_error = True
                print(f"error: {message_id} {part.filename}: {exc}")

        if saved_in_message == 0:
            links = tax.allowed_invoice_links(body_html, body_text)
            for index, url in enumerate(links, start=1):
                if is_hometax_url(url):
                    continue
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
                            record_processed_document(conn, source_conn, metadata)
                            saved_in_message += 1
                            continue
                        shutil.move(str(temp_pdf_path), str(pdf_path))
                    save_metadata(metadata_path, metadata)
                    record_processed_document(conn, source_conn, metadata)
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
