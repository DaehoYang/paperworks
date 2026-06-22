from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ReceiptRecord
from .paths import BASE_DIR, MEETING_DB, RECEIPT_DIR, SUMMARY_CSV, USED_RECEIPT_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_name TEXT NOT NULL UNIQUE,
  receipt_path TEXT NOT NULL,
  archived_path TEXT,
  file_sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'parsed',
  receipt_type TEXT NOT NULL DEFAULT 'unknown',
  generated_at TEXT NOT NULL,
  total_price INTEGER NOT NULL DEFAULT 0,
  store_name TEXT,
  address TEXT,
  origin TEXT,
  destination TEXT,
  transport_type TEXT,
  item_count INTEGER,
  food_count INTEGER,
  drink_count INTEGER,
  pair_id TEXT,
  ocr_engine TEXT,
  ocr_text_path TEXT,
  ocr_result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipts_type_generated ON receipts(receipt_type, generated_at);
CREATE INDEX IF NOT EXISTS idx_receipts_sha256 ON receipts(file_sha256);

CREATE TABLE IF NOT EXISTS generated_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'generated',
  output_pdf TEXT NOT NULL UNIQUE,
  topic TEXT,
  meeting_place TEXT,
  generated_at TEXT,
  attendee_count INTEGER,
  attendee_names_json TEXT NOT NULL DEFAULT '[]',
  total_price INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generated_documents_kind ON generated_documents(kind);
CREATE INDEX IF NOT EXISTS idx_generated_documents_generated ON generated_documents(generated_at);

CREATE TABLE IF NOT EXISTS generated_document_receipts (
  generated_document_id INTEGER NOT NULL,
  receipt_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (generated_document_id, receipt_id),
  FOREIGN KEY (generated_document_id) REFERENCES generated_documents(id),
  FOREIGN KEY (receipt_id) REFERENCES receipts(id)
);

CREATE TABLE IF NOT EXISTS email_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generated_document_id INTEGER,
  output_path TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  gmail_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'sent',
  sent_at TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(output_path, recipient, subject),
  FOREIGN KEY (generated_document_id) REFERENCES generated_documents(id)
);
CREATE INDEX IF NOT EXISTS idx_email_deliveries_output ON email_deliveries(output_path);
CREATE INDEX IF NOT EXISTS idx_email_deliveries_status ON email_deliveries(status);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path = MEETING_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def normalize_output_path(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return str(path.relative_to(BASE_DIR))
        except ValueError:
            return str(path)
    if path.parts and path.parts[0] == BASE_DIR.name:
        return str(Path(*path.parts[1:]))
    return str(path)


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_storage_paths(record: ReceiptRecord) -> tuple[str, str | None]:
    path = record.receipt_path.resolve()
    archived_path = str(path) if path.parent.resolve() == USED_RECEIPT_DIR.resolve() else None
    return str(path), archived_path


def upsert_receipt(conn: sqlite3.Connection, record: ReceiptRecord) -> int:
    now = utc_now()
    receipt_path, archived_path = receipt_storage_paths(record)
    values = {
        "file_name": record.file_name,
        "receipt_path": receipt_path,
        "archived_path": archived_path,
        "file_sha256": file_sha256(record.receipt_path),
        "status": record.status,
        "receipt_type": record.receipt_type,
        "generated_at": record.generated.strftime("%Y-%m-%d %H:%M:%S"),
        "total_price": record.total_price,
        "store_name": record.store_name,
        "address": record.address,
        "origin": record.origin,
        "destination": record.destination,
        "transport_type": record.transport_type,
        "item_count": record.item_count,
        "food_count": record.food_count,
        "drink_count": record.drink_count,
        "pair_id": record.pair_id,
        "ocr_engine": record.ocr_engine,
        "ocr_text_path": record.ocr_text_path,
        "ocr_result_json": record.ocr_result_json or "{}",
        "error": record.error,
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO receipts
        (file_name, receipt_path, archived_path, file_sha256, status, receipt_type, generated_at,
         total_price, store_name, address, origin, destination, transport_type, item_count,
         food_count, drink_count, pair_id, ocr_engine, ocr_text_path, ocr_result_json,
         error, created_at, updated_at)
        VALUES
        (:file_name, :receipt_path, :archived_path, :file_sha256, :status, :receipt_type, :generated_at,
         :total_price, :store_name, :address, :origin, :destination, :transport_type, :item_count,
         :food_count, :drink_count, :pair_id, :ocr_engine, :ocr_text_path, :ocr_result_json,
         :error, :created_at, :updated_at)
        ON CONFLICT(file_name) DO UPDATE SET
          receipt_path=excluded.receipt_path,
          archived_path=excluded.archived_path,
          file_sha256=COALESCE(excluded.file_sha256, receipts.file_sha256),
          status=excluded.status,
          receipt_type=excluded.receipt_type,
          generated_at=excluded.generated_at,
          total_price=excluded.total_price,
          store_name=excluded.store_name,
          address=excluded.address,
          origin=excluded.origin,
          destination=excluded.destination,
          transport_type=excluded.transport_type,
          item_count=excluded.item_count,
          food_count=excluded.food_count,
          drink_count=excluded.drink_count,
          pair_id=excluded.pair_id,
          ocr_engine=excluded.ocr_engine,
          ocr_text_path=excluded.ocr_text_path,
          ocr_result_json=excluded.ocr_result_json,
          error=excluded.error,
          updated_at=excluded.updated_at
        """,
        values,
    )
    row = conn.execute("SELECT id FROM receipts WHERE file_name=?", (record.file_name,)).fetchone()
    return int(row["id"])


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


def receipt_path_from_row(row: sqlite3.Row) -> Path:
    for value in (row["archived_path"], row["receipt_path"]):
        if value and Path(value).exists():
            return Path(value).resolve()
    receipt_path = RECEIPT_DIR / row["file_name"]
    if receipt_path.exists():
        return receipt_path.resolve()
    used_path = USED_RECEIPT_DIR / row["file_name"]
    if used_path.exists():
        return used_path.resolve()
    return Path(row["receipt_path"]).resolve()


def row_to_record(row: sqlite3.Row) -> ReceiptRecord:
    return ReceiptRecord(
        file_name=row["file_name"],
        receipt_path=receipt_path_from_row(row),
        generated=parse_datetime(row["generated_at"]),
        total_price=int(row["total_price"] or 0),
        store_name=row["store_name"] or "",
        address=row["address"] or "",
        receipt_type=row["receipt_type"] or "unknown",
        transport_type=row["transport_type"] or "",
        origin=row["origin"] or "",
        destination=row["destination"] or "",
        item_count=safe_int(row["item_count"]),
        food_count=safe_int(row["food_count"]),
        drink_count=safe_int(row["drink_count"]),
        ocr_engine=row["ocr_engine"] or "manual",
        ocr_text_path=row["ocr_text_path"] or "",
        ocr_result_json=row["ocr_result_json"] or "{}",
        status=row["status"] or "parsed",
        pair_id=row["pair_id"] or "",
        document_type=document_type_for_receipt(conn=None, row=row),
        output_pdf=output_pdf_for_receipt(conn=None, row=row),
        error=row["error"] or "",
    )


def generated_document_lookup(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT r.id AS receipt_id, gd.*
        FROM receipts r
        JOIN generated_document_receipts gdr ON gdr.receipt_id = r.id
        JOIN generated_documents gd ON gd.id = gdr.generated_document_id
        WHERE gd.status='generated'
        """
    ).fetchall()
    return {int(row["receipt_id"]): row for row in rows}


def document_type_for_receipt(conn: sqlite3.Connection | None, row: sqlite3.Row, generated: sqlite3.Row | None = None) -> str:
    if generated is None and conn is not None:
        generated = generated_document_lookup(conn).get(int(row["id"]))
    if generated is None:
        return "trip" if row["pair_id"] else ""
    if generated["kind"] == "trip":
        return "trip"
    return generated["topic"] or "meeting"


def output_pdf_for_receipt(conn: sqlite3.Connection | None, row: sqlite3.Row, generated: sqlite3.Row | None = None) -> str:
    if generated is None and conn is not None:
        generated = generated_document_lookup(conn).get(int(row["id"]))
    return "" if generated is None else str(generated["output_pdf"] or "")


def load_receipts(conn: sqlite3.Connection) -> list[ReceiptRecord]:
    generated_by_receipt = generated_document_lookup(conn)
    records: list[ReceiptRecord] = []
    for row in conn.execute("SELECT * FROM receipts ORDER BY generated_at, file_name"):
        generated = generated_by_receipt.get(int(row["id"]))
        record = row_to_record(row)
        if generated is not None:
            record = ReceiptRecord(
                **{
                    **record.__dict__,
                    "document_type": document_type_for_receipt(conn, row, generated),
                    "output_pdf": output_pdf_for_receipt(conn, row, generated),
                }
            )
        records.append(record)
    return records


def summary_rows(path: Path = SUMMARY_CSV) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row.get("file_name", ""): row for row in csv.DictReader(handle)}


def attendee_names_json(raw: str) -> str:
    names = [name.strip() for name in raw.split(";") if name.strip()]
    return json.dumps(names, ensure_ascii=False)


def generated_document_values(output_pdf: str, group: list[ReceiptRecord], summaries: dict[str, dict[str, str]]) -> dict[str, object]:
    now = utc_now()
    is_trip = any(record.document_type == "trip" or record.pair_id for record in group)
    first = sorted(group, key=lambda item: (item.generated, item.file_name))[0]
    if is_trip:
        return {
            "kind": "trip",
            "status": "generated",
            "output_pdf": output_pdf,
            "topic": "trip",
            "meeting_place": "",
            "generated_at": first.generated.strftime("%Y-%m-%d %H:%M:%S"),
            "attendee_count": None,
            "attendee_names_json": "[]",
            "total_price": sum(record.total_price for record in group),
            "created_at": now,
            "updated_at": now,
        }

    summary = summaries.get(first.file_name, {})
    return {
        "kind": "meeting",
        "status": "generated",
        "output_pdf": output_pdf,
        "topic": summary.get("topic") or first.document_type or "meeting",
        "meeting_place": summary.get("meeting_place") or "",
        "generated_at": first.generated.strftime("%Y-%m-%d %H:%M:%S"),
        "attendee_count": safe_int(summary.get("attendee_count")),
        "attendee_names_json": attendee_names_json(summary.get("attendee_names") or ""),
        "total_price": first.total_price,
        "created_at": now,
        "updated_at": now,
    }


def upsert_generated_document(
    conn: sqlite3.Connection,
    output_pdf: str,
    group: list[ReceiptRecord],
    receipt_ids: dict[str, int],
    summaries: dict[str, dict[str, str]],
) -> int:
    values = generated_document_values(output_pdf, group, summaries)
    conn.execute(
        """
        INSERT INTO generated_documents
        (kind, status, output_pdf, topic, meeting_place, generated_at, attendee_count,
         attendee_names_json, total_price, created_at, updated_at)
        VALUES
        (:kind, :status, :output_pdf, :topic, :meeting_place, :generated_at, :attendee_count,
         :attendee_names_json, :total_price, :created_at, :updated_at)
        ON CONFLICT(output_pdf) DO UPDATE SET
          kind=excluded.kind,
          status=excluded.status,
          topic=excluded.topic,
          meeting_place=excluded.meeting_place,
          generated_at=excluded.generated_at,
          attendee_count=excluded.attendee_count,
          attendee_names_json=excluded.attendee_names_json,
          total_price=excluded.total_price,
          updated_at=excluded.updated_at
        """,
        values,
    )
    row = conn.execute("SELECT id FROM generated_documents WHERE output_pdf=?", (output_pdf,)).fetchone()
    document_id = int(row["id"])
    now = utc_now()
    for record in group:
        conn.execute("DELETE FROM generated_document_receipts WHERE receipt_id=?", (receipt_ids[record.file_name],))
    for record in group:
        conn.execute(
            """
            INSERT OR IGNORE INTO generated_document_receipts
            (generated_document_id, receipt_id, created_at)
            VALUES (?, ?, ?)
            """,
            (document_id, receipt_ids[record.file_name], now),
        )
    return document_id


def generated_document_id_for_output(conn: sqlite3.Connection, output_path: str | Path) -> int | None:
    normalized = normalize_output_path(output_path)
    candidates = [normalized]
    if not Path(normalized).is_absolute():
        candidates.append(str(BASE_DIR / normalized))
    for candidate in candidates:
        row = conn.execute("SELECT id FROM generated_documents WHERE output_pdf=?", (candidate,)).fetchone()
        if row:
            return int(row["id"])
    return None


def upsert_email_delivery(
    conn: sqlite3.Connection,
    *,
    output_path: str | Path,
    recipient: str,
    subject: str,
    gmail_message_id: str | None = None,
    status: str = "sent",
    sent_at: str | None = None,
    note: str | None = None,
) -> int:
    now = utc_now()
    normalized = normalize_output_path(output_path)
    values = {
        "generated_document_id": generated_document_id_for_output(conn, normalized),
        "output_path": normalized,
        "recipient": recipient,
        "subject": subject,
        "gmail_message_id": gmail_message_id,
        "status": status,
        "sent_at": sent_at or now,
        "note": note,
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO email_deliveries
        (generated_document_id, output_path, recipient, subject, gmail_message_id,
         status, sent_at, note, created_at, updated_at)
        VALUES
        (:generated_document_id, :output_path, :recipient, :subject, :gmail_message_id,
         :status, :sent_at, :note, :created_at, :updated_at)
        ON CONFLICT(output_path, recipient, subject) DO UPDATE SET
          generated_document_id=COALESCE(excluded.generated_document_id, email_deliveries.generated_document_id),
          gmail_message_id=COALESCE(excluded.gmail_message_id, email_deliveries.gmail_message_id),
          status=excluded.status,
          sent_at=excluded.sent_at,
          note=COALESCE(excluded.note, email_deliveries.note),
          updated_at=excluded.updated_at
        """,
        values,
    )
    row = conn.execute(
        "SELECT id FROM email_deliveries WHERE output_path=? AND recipient=? AND subject=?",
        (normalized, recipient, subject),
    ).fetchone()
    return int(row["id"])


def mark_output_emailed(
    output_path: str | Path,
    *,
    recipient: str,
    subject: str,
    gmail_message_id: str | None = None,
    status: str = "sent",
    sent_at: str | None = None,
    note: str | None = None,
    db_path: Path = MEETING_DB,
) -> int:
    conn = connect(db_path)
    try:
        delivery_id = upsert_email_delivery(
            conn,
            output_path=output_path,
            recipient=recipient,
            subject=subject,
            gmail_message_id=gmail_message_id,
            status=status,
            sent_at=sent_at,
            note=note,
        )
        conn.commit()
        return delivery_id
    finally:
        conn.close()


def sync_records(
    records: Iterable[ReceiptRecord],
    *,
    db_path: Path = MEETING_DB,
    summaries: dict[str, dict[str, str]] | None = None,
) -> None:
    records = list(records)
    conn = connect(db_path)
    try:
        receipt_ids = {record.file_name: upsert_receipt(conn, record) for record in records}
        grouped: dict[str, list[ReceiptRecord]] = {}
        for record in records:
            if record.output_pdf:
                grouped.setdefault(record.output_pdf, []).append(record)
        summaries = summaries if summaries is not None else summary_rows()
        for output_pdf, group in grouped.items():
            upsert_generated_document(conn, output_pdf, group, receipt_ids, summaries)
        conn.commit()
    finally:
        conn.close()


def has_receipts(db_path: Path = MEETING_DB) -> bool:
    if not db_path.exists():
        return False
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM receipts").fetchone()
        return bool(row and row["count"])
    finally:
        conn.close()


def load_records(db_path: Path = MEETING_DB) -> list[ReceiptRecord]:
    conn = connect(db_path)
    try:
        return load_receipts(conn)
    finally:
        conn.close()
