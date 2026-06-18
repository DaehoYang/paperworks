from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_type TEXT NOT NULL,
  all_doc_types_json TEXT NOT NULL DEFAULT '[]',
  vendor TEXT,
  normalized_vendor TEXT,
  document_number TEXT,
  item_code TEXT,
  item_count INTEGER,
  item_prices_json TEXT NOT NULL DEFAULT '[]',
  issue_date TEXT,
  amount INTEGER,
  currency TEXT DEFAULT 'KRW',
  source TEXT NOT NULL DEFAULT 'gmail',
  gmail_message_id TEXT,
  gmail_thread_id TEXT,
  gmail_url TEXT,
  email_from TEXT,
  email_subject TEXT,
  email_date TEXT,
  source_type TEXT,
  source_attachment_id TEXT,
  source_filename TEXT,
  source_mime_type TEXT,
  source_size INTEGER,
  source_sha256 TEXT,
  source_link TEXT,
  source_key TEXT,
  file_path TEXT NOT NULL,
  json_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_sha256_type ON documents(sha256, doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_vendor_date ON documents(normalized_vendor, issue_date);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_number ON documents(document_number);
CREATE INDEX IF NOT EXISTS idx_documents_item_code ON documents(item_code);
CREATE INDEX IF NOT EXISTS idx_documents_source_key ON documents(source_key);

CREATE TABLE IF NOT EXISTS processed_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'gmail',
  gmail_message_id TEXT,
  gmail_thread_id TEXT,
  source_type TEXT,
  source_attachment_id TEXT,
  source_filename TEXT,
  source_mime_type TEXT,
  source_size INTEGER,
  source_sha256 TEXT,
  source_link TEXT,
  doc_type TEXT,
  document_id INTEGER,
  status TEXT NOT NULL DEFAULT 'processed',
  reason TEXT,
  processed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (document_id) REFERENCES documents(id)
);
CREATE INDEX IF NOT EXISTS idx_processed_sources_gmail_message ON processed_sources(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_processed_sources_attachment ON processed_sources(gmail_message_id, source_attachment_id);
CREATE INDEX IF NOT EXISTS idx_processed_sources_filename ON processed_sources(gmail_message_id, source_filename, source_size);
CREATE INDEX IF NOT EXISTS idx_processed_sources_status ON processed_sources(status);

CREATE TABLE IF NOT EXISTS purchase_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_dir TEXT NOT NULL UNIQUE,
  case_name TEXT NOT NULL,
  case_date TEXT,
  vendor TEXT,
  normalized_vendor TEXT,
  document_number TEXT,
  item_code TEXT,
  amount INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchase_cases_vendor_date ON purchase_cases(normalized_vendor, case_date);

CREATE TABLE IF NOT EXISTS purchase_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  purchase_case_id INTEGER NOT NULL,
  document_id INTEGER,
  doc_type TEXT NOT NULL,
  local_path TEXT,
  match_score REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'candidate',
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (purchase_case_id) REFERENCES purchase_cases(id),
  FOREIGN KEY (document_id) REFERENCES documents(id)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    ensure_columns(
        conn,
        "documents",
        {
            "all_doc_types_json": "TEXT NOT NULL DEFAULT '[]'",
            "item_count": "INTEGER",
            "item_prices_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_type": "TEXT",
            "source_attachment_id": "TEXT",
            "source_mime_type": "TEXT",
            "source_size": "INTEGER",
            "source_sha256": "TEXT",
            "source_key": "TEXT",
        },
    )
    conn.commit()
    return conn


def upsert_document(conn: sqlite3.Connection, metadata: dict) -> int:
    now = utc_now()
    all_doc_types = metadata.get("all_doc_types") or [metadata.get("doc_type")]
    values = {
        "doc_type": metadata.get("doc_type") or "unknown",
        "all_doc_types_json": json.dumps([x for x in all_doc_types if x], ensure_ascii=False),
        "vendor": metadata.get("vendor"),
        "normalized_vendor": metadata.get("normalized_vendor"),
        "document_number": metadata.get("document_number"),
        "item_code": metadata.get("item_code"),
        "item_count": metadata.get("item_count"),
        "item_prices_json": json.dumps(metadata.get("item_prices") or [], ensure_ascii=False),
        "issue_date": metadata.get("issue_date"),
        "amount": metadata.get("amount"),
        "currency": metadata.get("currency") or "KRW",
        "source": metadata.get("source") or "gmail",
        "gmail_message_id": metadata.get("message_id"),
        "gmail_thread_id": metadata.get("thread_id"),
        "gmail_url": metadata.get("gmail_url"),
        "email_from": metadata.get("from"),
        "email_subject": metadata.get("subject"),
        "email_date": metadata.get("email_date"),
        "source_type": metadata.get("source_type"),
        "source_attachment_id": metadata.get("source_attachment_id"),
        "source_filename": metadata.get("source_filename"),
        "source_mime_type": metadata.get("source_mime_type"),
        "source_size": metadata.get("source_size"),
        "source_sha256": metadata.get("source_sha256"),
        "source_link": metadata.get("source_link"),
        "source_key": metadata.get("source_key") or source_key_from_metadata(metadata),
        "file_path": metadata.get("saved_pdf") or metadata.get("file_path"),
        "json_path": metadata.get("json_path"),
        "sha256": metadata.get("sha256") or metadata.get("source_sha256"),
        "confidence": float(metadata.get("confidence") or 0),
        "status": metadata.get("status") or "active",
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(":" + key for key in values)
    updates = ", ".join(
        f"{key}=excluded.{key}" for key in values if key not in {"created_at"}
    )
    conn.execute(
        f"""
        INSERT INTO documents ({columns})
        VALUES ({placeholders})
        ON CONFLICT(sha256, doc_type) DO UPDATE SET {updates}
        """,
        values,
    )
    row = conn.execute(
        "SELECT id FROM documents WHERE sha256=? AND doc_type=?",
        (values["sha256"], values["doc_type"]),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def source_key(
    *,
    source: str = "gmail",
    gmail_message_id: str | None = None,
    source_type: str | None = None,
    source_attachment_id: str | None = None,
    source_filename: str | None = None,
    source_size: int | None = None,
    source_link: str | None = None,
    index: int | None = None,
) -> str | None:
    if not gmail_message_id:
        return None
    kind = source_type or "attachment"
    if kind == "link" and source_link:
        return f"{source}:link:{gmail_message_id}:{stable_hash(source_link)}"
    if source_attachment_id:
        return f"{source}:attachment:{gmail_message_id}:{source_attachment_id}"
    filename = source_filename or ""
    size = "" if source_size is None else str(source_size)
    suffix = f"{filename}:{size}:{'' if index is None else index}"
    return f"{source}:attachment:{gmail_message_id}:{stable_hash(suffix)}"


def source_key_from_metadata(metadata: dict) -> str | None:
    return source_key(
        source=metadata.get("source") or "gmail",
        gmail_message_id=metadata.get("message_id") or metadata.get("gmail_message_id"),
        source_type=metadata.get("source_type"),
        source_attachment_id=metadata.get("source_attachment_id"),
        source_filename=metadata.get("source_filename"),
        source_size=metadata.get("source_size"),
        source_link=metadata.get("source_link"),
    )


def upsert_processed_source(
    conn: sqlite3.Connection,
    metadata: dict,
    *,
    document_id: int | None = None,
    status: str = "processed",
    reason: str | None = None,
) -> int | None:
    key = metadata.get("source_key") or source_key_from_metadata(metadata)
    if not key:
        return None
    now = utc_now()
    values = {
        "source_key": key,
        "source": metadata.get("source") or "gmail",
        "gmail_message_id": metadata.get("message_id") or metadata.get("gmail_message_id"),
        "gmail_thread_id": metadata.get("thread_id") or metadata.get("gmail_thread_id"),
        "source_type": metadata.get("source_type"),
        "source_attachment_id": metadata.get("source_attachment_id"),
        "source_filename": metadata.get("source_filename"),
        "source_mime_type": metadata.get("source_mime_type"),
        "source_size": metadata.get("source_size"),
        "source_sha256": metadata.get("source_sha256"),
        "source_link": metadata.get("source_link"),
        "doc_type": metadata.get("doc_type"),
        "document_id": document_id,
        "status": status,
        "reason": reason,
        "processed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO processed_sources
        (source_key, source, gmail_message_id, gmail_thread_id, source_type, source_attachment_id,
         source_filename, source_mime_type, source_size, source_sha256, source_link, doc_type,
         document_id, status, reason, processed_at, created_at, updated_at)
        VALUES
        (:source_key, :source, :gmail_message_id, :gmail_thread_id, :source_type, :source_attachment_id,
         :source_filename, :source_mime_type, :source_size, :source_sha256, :source_link, :doc_type,
         :document_id, :status, :reason, :processed_at, :created_at, :updated_at)
        ON CONFLICT(source_key) DO UPDATE SET
          source=excluded.source,
          gmail_message_id=excluded.gmail_message_id,
          gmail_thread_id=excluded.gmail_thread_id,
          source_type=excluded.source_type,
          source_attachment_id=excluded.source_attachment_id,
          source_filename=excluded.source_filename,
          source_mime_type=excluded.source_mime_type,
          source_size=excluded.source_size,
          source_sha256=excluded.source_sha256,
          source_link=excluded.source_link,
          doc_type=excluded.doc_type,
          document_id=COALESCE(excluded.document_id, processed_sources.document_id),
          status=excluded.status,
          reason=excluded.reason,
          processed_at=excluded.processed_at,
          updated_at=excluded.updated_at
        """,
        values,
    )
    row = conn.execute("SELECT id FROM processed_sources WHERE source_key=?", (key,)).fetchone()
    conn.commit()
    return int(row["id"]) if row else None


def processed_source_row(
    conn: sqlite3.Connection,
    *,
    source_key_value: str | None = None,
    gmail_message_id: str | None = None,
    source_type: str | None = None,
    source_attachment_id: str | None = None,
    source_filename: str | None = None,
    source_size: int | None = None,
    source_link: str | None = None,
) -> sqlite3.Row | None:
    base_select = """
        SELECT ps.*, d.file_path AS document_file_path
        FROM processed_sources ps
        LEFT JOIN documents d ON d.id = ps.document_id
        WHERE ps.status='processed'
    """
    if source_key_value:
        row = conn.execute(base_select + " AND ps.source_key=?", (source_key_value,)).fetchone()
        if row:
            return row
    if not gmail_message_id:
        return None
    if source_type == "link" and source_link:
        return conn.execute(
            base_select + " AND ps.gmail_message_id=? AND ps.source_type='link' AND ps.source_link=?",
            (gmail_message_id, source_link),
        ).fetchone()
    if source_attachment_id:
        row = conn.execute(
            base_select + " AND ps.gmail_message_id=? AND ps.source_attachment_id=?",
            (gmail_message_id, source_attachment_id),
        ).fetchone()
        if row:
            return row
    if source_filename:
        if source_size is None:
            return conn.execute(
                base_select + " AND ps.gmail_message_id=? AND ps.source_filename=?",
                (gmail_message_id, source_filename),
            ).fetchone()
        return conn.execute(
            base_select + " AND ps.gmail_message_id=? AND ps.source_filename=? AND (ps.source_size=? OR ps.source_size IS NULL)",
            (gmail_message_id, source_filename, source_size),
        ).fetchone()
    return None


def processed_source_has_existing_document(row: sqlite3.Row | None) -> bool:
    if not row:
        return False
    file_path = row["document_file_path"] if "document_file_path" in row.keys() else None
    return bool(file_path and Path(file_path).exists())


def backfill_processed_sources_from_documents(conn: sqlite3.Connection) -> int:
    count = 0
    for row in conn.execute("SELECT * FROM documents WHERE status='active'"):
        metadata = dict(row)
        metadata["message_id"] = metadata.get("gmail_message_id")
        metadata["thread_id"] = metadata.get("gmail_thread_id")
        metadata["from"] = metadata.get("email_from")
        if not source_key_from_metadata(metadata):
            continue
        source_id = upsert_processed_source(conn, metadata, document_id=int(row["id"]), reason="backfill documents")
        if source_id:
            count += 1
    return count


def upsert_purchase_case(conn: sqlite3.Connection, case: dict) -> int:
    now = utc_now()
    values = {
        "case_dir": case["case_dir"],
        "case_name": case["case_name"],
        "case_date": case.get("case_date"),
        "vendor": case.get("vendor"),
        "normalized_vendor": case.get("normalized_vendor"),
        "document_number": case.get("document_number"),
        "item_code": case.get("item_code"),
        "amount": case.get("amount"),
        "status": case.get("status") or "active",
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO purchase_cases
        (case_dir, case_name, case_date, vendor, normalized_vendor, document_number, item_code, amount, status, created_at, updated_at)
        VALUES
        (:case_dir, :case_name, :case_date, :vendor, :normalized_vendor, :document_number, :item_code, :amount, :status, :created_at, :updated_at)
        ON CONFLICT(case_dir) DO UPDATE SET
          case_name=excluded.case_name,
          case_date=excluded.case_date,
          vendor=excluded.vendor,
          normalized_vendor=excluded.normalized_vendor,
          document_number=excluded.document_number,
          item_code=excluded.item_code,
          amount=excluded.amount,
          status=excluded.status,
          updated_at=excluded.updated_at
        """,
        values,
    )
    row = conn.execute("SELECT id FROM purchase_cases WHERE case_dir=?", (case["case_dir"],)).fetchone()
    conn.commit()
    return int(row["id"])


def replace_local_purchase_documents(conn: sqlite3.Connection, purchase_case_id: int, docs: dict[str, list[Path]]) -> None:
    conn.execute(
        "DELETE FROM purchase_documents WHERE purchase_case_id=? AND status='local'",
        (purchase_case_id,),
    )
    now = utc_now()
    for doc_type, paths in docs.items():
        for path in paths:
            conn.execute(
                """
                INSERT INTO purchase_documents
                (purchase_case_id, doc_type, local_path, match_score, status, reason, created_at, updated_at)
                VALUES (?, ?, ?, 1.0, 'local', 'local filename match', ?, ?)
                """,
                (purchase_case_id, doc_type, str(path), now, now),
            )
    conn.commit()


def load_documents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM documents WHERE status='active'"))


def load_json_archive(paths: Iterable[Path]) -> list[dict]:
    docs: list[dict] = []
    for base in paths:
        if not base.exists():
            continue
        for json_path in sorted(base.glob("*.json")):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            data.setdefault("json_path", str(json_path))
            data.setdefault("file_path", data.get("saved_pdf"))
            docs.append(data)
    return docs
