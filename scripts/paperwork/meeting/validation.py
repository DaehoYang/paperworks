from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import config
from .documents import trip
from .models import ReceiptRecord
from .paths import BASE_DIR, MEETING_DB, WORKSPACE_DIR


MEETING_RECEIPT_TYPES = {"food_drink", "restaurant", "cafe", "meal", "drink"}
KNOWN_RECEIPT_TYPES = MEETING_RECEIPT_TYPES | {"transport", "lodging", "office_supply", "medical", "fuel", "other", "unknown"}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(True, [])

    @classmethod
    def failure(cls, errors: Iterable[str]) -> "ValidationResult":
        return cls(False, list(errors))


def combine_results(*results: ValidationResult) -> ValidationResult:
    errors = [error for result in results for error in result.errors]
    return ValidationResult(not errors, errors)


def output_pdf_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "output":
        return BASE_DIR / path
    return WORKSPACE_DIR / path


def validate_receipt_record(record: ReceiptRecord) -> ValidationResult:
    errors: list[str] = []
    if not record.file_name:
        errors.append("receipt file_name is required")
    if not record.receipt_path.exists():
        errors.append(f"receipt file does not exist: {record.receipt_path}")
    if record.generated > datetime.now():
        errors.append(f"receipt generated time is in the future: {record.generated:%Y-%m-%d %H:%M:%S}")
    if record.total_price <= 0:
        errors.append("receipt total_price must be positive")
    if record.receipt_type not in KNOWN_RECEIPT_TYPES:
        errors.append(f"unknown receipt_type: {record.receipt_type}")
    if record.receipt_type in MEETING_RECEIPT_TYPES and not (record.store_name.strip() or record.address.strip()):
        errors.append("meeting receipt must include store_name or address")
    if record.receipt_type == "transport":
        if not record.origin.strip() or not record.destination.strip():
            errors.append("transport receipt must include origin and destination")
        if record.origin.strip() and record.destination.strip() and not (trip.is_seoul(record.origin) or trip.is_seoul(record.destination)):
            errors.append("transport receipt route must include a Seoul endpoint")
    return ValidationResult(not errors, errors)


def validate_meeting_generation_input(record: ReceiptRecord) -> ValidationResult:
    errors = validate_receipt_record(record).errors
    if record.receipt_type not in MEETING_RECEIPT_TYPES:
        errors.append(f"receipt_type is not meeting-compatible: {record.receipt_type}")
    try:
        count = config.attendee_count(
            record.total_price,
            record.item_count,
            record.food_count,
            record.drink_count,
            record.store_name,
        )
    except Exception as exc:
        errors.append(f"could not calculate attendee count: {exc}")
    else:
        rules = config.attendee_rules()
        min_attendees = int(rules.get("min_attendees") or 1)
        max_attendees = int(rules.get("max_attendees") or max(min_attendees, count))
        if count < min_attendees or count > max_attendees:
            errors.append(f"attendee count is outside configured range: {count}")
        if count > len(config.members()):
            errors.append(f"attendee count exceeds configured member count: {count}")
    return ValidationResult(not errors, errors)


def validate_trip_generation_input(outbound: ReceiptRecord, inbound: ReceiptRecord) -> ValidationResult:
    errors = validate_receipt_record(outbound).errors + validate_receipt_record(inbound).errors
    if outbound.receipt_type != "transport":
        errors.append(f"outbound receipt_type must be transport: {outbound.file_name}")
    if inbound.receipt_type != "transport":
        errors.append(f"inbound receipt_type must be transport: {inbound.file_name}")
    try:
        trip.validate_pair(outbound, inbound)
    except ValueError as exc:
        errors.append(str(exc))
    return ValidationResult(not errors, errors)


def validate_generated_records(records: Iterable[ReceiptRecord]) -> ValidationResult:
    records = list(records)
    errors: list[str] = []
    for record in records:
        if record.status != "generated":
            errors.append(f"generated record has non-generated status: {record.file_name} status={record.status}")
        if not record.document_type:
            errors.append(f"generated record has no document_type: {record.file_name}")
        if not record.output_pdf:
            errors.append(f"generated record has no output file: {record.file_name}")
        elif not output_pdf_path(record.output_pdf).exists():
            errors.append(f"generated output file does not exist: {record.output_pdf}")
        if not record.receipt_path.exists():
            errors.append(f"generated receipt file does not exist: {record.receipt_path}")

    trip_groups: dict[str, list[ReceiptRecord]] = {}
    for record in records:
        if record.document_type == "trip":
            trip_groups.setdefault(record.output_pdf, []).append(record)
    for output_pdf, group in trip_groups.items():
        if len(group) != 2:
            errors.append(f"trip output must be linked to exactly two receipts: {output_pdf}")
        elif group[0].pair_id != group[1].pair_id:
            errors.append(f"trip receipt pair_id mismatch: {output_pdf}")
    return ValidationResult(not errors, errors)


def validate_database(db_path: Path = MEETING_DB) -> ValidationResult:
    if not db_path.exists():
        return ValidationResult.failure([f"meeting database does not exist: {db_path}"])
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    errors: list[str] = []
    try:
        generated_without_link = conn.execute(
            """
            SELECT r.file_name
            FROM receipts r
            LEFT JOIN generated_document_receipts gdr ON gdr.receipt_id = r.id
            WHERE r.status='generated' AND gdr.receipt_id IS NULL
            """
        ).fetchall()
        for row in generated_without_link:
            errors.append(f"generated receipt has no generated_document link: {row['file_name']}")

        for row in conn.execute("SELECT output_pdf FROM generated_documents WHERE status='generated'"):
            if not output_pdf_path(row["output_pdf"]).exists():
                errors.append(f"generated document output file does not exist: {row['output_pdf']}")

        for row in conn.execute("SELECT file_name, receipt_path, archived_path FROM receipts"):
            receipt_path = Path(row["receipt_path"]) if row["receipt_path"] else None
            archived_path = Path(row["archived_path"]) if row["archived_path"] else None
            if not ((receipt_path and receipt_path.exists()) or (archived_path and archived_path.exists())):
                errors.append(f"receipt file path does not exist: {row['file_name']}")
    finally:
        conn.close()
    return ValidationResult(not errors, errors)


def format_errors(result: ValidationResult) -> str:
    return "; ".join(result.errors)
