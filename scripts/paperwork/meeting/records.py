from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .models import ReceiptRecord
from .paths import RECORDS_CSV, RECEIPT_DIR, SUMMARY_CSV, USED_RECEIPT_DIR


LEDGER_HEADER = [
    "file_name",
    "status",
    "receipt_type",
    "generated",
    "total_price",
    "store_name",
    "address",
    "origin",
    "destination",
    "transport_type",
    "item_count",
    "food_count",
    "drink_count",
    "pair_id",
    "document_type",
    "output_pdf",
    "ocr_engine",
    "ocr_text_path",
    "ocr_result_json",
    "error",
]

SUMMARY_HEADER = [
    "file_name",
    "total_price",
    "store_name",
    "address",
    "meeting_place",
    "generated",
    "topic",
    "attendee_count",
    "item_count",
    "food_count",
    "drink_count",
    "attendee_names",
    "receipt_type",
    "ocr_engine",
]


def resolve_receipt_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() and path.exists():
        path = path.resolve()
    elif not path.is_absolute():
        path = RECEIPT_DIR / path
        if not path.exists():
            used_path = USED_RECEIPT_DIR / raw
            if used_path.exists():
                path = used_path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def parse_datetime(raw: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported datetime format: {raw}")


def safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value).replace(",", ""))


def record_to_row(record: ReceiptRecord) -> dict[str, str]:
    return {
        "file_name": record.file_name,
        "status": record.status,
        "receipt_type": record.receipt_type,
        "generated": record.generated.strftime("%Y-%m-%d %H:%M:%S"),
        "total_price": str(record.total_price),
        "store_name": record.store_name,
        "address": record.address,
        "origin": record.origin,
        "destination": record.destination,
        "transport_type": record.transport_type,
        "item_count": "" if record.item_count is None else str(record.item_count),
        "food_count": "" if record.food_count is None else str(record.food_count),
        "drink_count": "" if record.drink_count is None else str(record.drink_count),
        "pair_id": record.pair_id,
        "document_type": record.document_type,
        "output_pdf": record.output_pdf,
        "ocr_engine": record.ocr_engine,
        "ocr_text_path": record.ocr_text_path,
        "ocr_result_json": record.ocr_result_json,
        "error": record.error,
    }


def row_to_record(row: dict[str, str]) -> ReceiptRecord:
    path = resolve_receipt_path(row["file_name"])
    return ReceiptRecord(
        file_name=row["file_name"],
        receipt_path=path,
        generated=parse_datetime(row["generated"]),
        total_price=int(row.get("total_price") or 0),
        store_name=row.get("store_name", ""),
        address=row.get("address", ""),
        receipt_type=row.get("receipt_type", "unknown"),
        transport_type=row.get("transport_type", ""),
        origin=row.get("origin", ""),
        destination=row.get("destination", ""),
        item_count=safe_int(row.get("item_count")),
        food_count=safe_int(row.get("food_count")),
        drink_count=safe_int(row.get("drink_count")),
        ocr_engine=row.get("ocr_engine", "manual"),
        ocr_text_path=row.get("ocr_text_path", ""),
        ocr_result_json=row.get("ocr_result_json", "{}"),
        status=row.get("status", "parsed"),
        pair_id=row.get("pair_id", ""),
        document_type=row.get("document_type", ""),
        output_pdf=row.get("output_pdf", ""),
        error=row.get("error", ""),
    )


def read_records(path: Path = RECORDS_CSV) -> list[ReceiptRecord]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row_to_record(row) for row in csv.DictReader(handle)]


def write_records(records: list[ReceiptRecord], path: Path = RECORDS_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_HEADER, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record_to_row(record))


def upsert_records(new_records: list[ReceiptRecord], path: Path = RECORDS_CSV) -> None:
    records = read_records(path)
    by_name = {record.file_name: idx for idx, record in enumerate(records)}
    for record in new_records:
        if record.file_name in by_name:
            records[by_name[record.file_name]] = record
        else:
            records.append(record)
    write_records(records, path)


def update_summary(record: ReceiptRecord, meeting_place: str, topic: str, attendee_count: int, attendee_names: str) -> None:
    rows: list[dict[str, str]] = []
    if SUMMARY_CSV.exists():
        with SUMMARY_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    new_row = {
        "file_name": record.file_name,
        "total_price": str(record.total_price),
        "store_name": record.store_name,
        "address": record.address,
        "meeting_place": meeting_place,
        "generated": record.generated.strftime("%Y-%m-%d %H:%M:%S"),
        "topic": topic,
        "attendee_count": str(attendee_count),
        "item_count": "" if record.item_count is None else str(record.item_count),
        "food_count": "" if record.food_count is None else str(record.food_count),
        "drink_count": "" if record.drink_count is None else str(record.drink_count),
        "attendee_names": attendee_names,
        "receipt_type": record.receipt_type,
        "ocr_engine": record.ocr_engine,
    }
    for idx, row in enumerate(rows):
        if row.get("file_name") == record.file_name:
            rows[idx] = {**row, **new_row}
            break
    else:
        rows.append(new_row)
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_HEADER})


def mark_error(record: ReceiptRecord, message: str) -> ReceiptRecord:
    return replace(record, status="error", error=message)
