from __future__ import annotations

from pathlib import Path

from scripts.gui.services import files as file_services
from scripts.gui.services.paths import MEETING_DIR, ROOT_DIR, repo_relative
from scripts.paperwork.meeting import db as meeting_db


MEETING_RECEIPT_SUFFIXES = {".pdf", *file_services.IMAGE_EXTENSIONS}


def pending_receipt_names(receipt_dir: Path | None = None) -> set[str]:
    receipt_dir = receipt_dir or MEETING_DIR / "receipt"
    pending: set[str] = set()
    if receipt_dir.exists() and receipt_dir.is_dir():
        pending.update(
            path.name
            for path in receipt_dir.iterdir()
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in MEETING_RECEIPT_SUFFIXES
        )
    if meeting_db.MEETING_DB.exists():
        conn = meeting_db.connect()
        try:
            rows = conn.execute(
                """
                SELECT file_name
                FROM receipts
                WHERE status IN ('parsed', 'review', 'pending_trip', 'error')
                """
            ).fetchall()
            pending.update(str(row["file_name"]) for row in rows)
        finally:
            conn.close()
    return pending


def status_summary() -> dict[str, int]:
    pending = pending_receipt_names()
    ready_to_email = 0
    emailed = 0
    total_outputs = 0
    if meeting_db.MEETING_DB.exists():
        conn = meeting_db.connect()
        try:
            total_outputs = int(
                conn.execute("SELECT COUNT(*) AS count FROM generated_documents WHERE status='generated'").fetchone()["count"]
            )
            emailed = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT output_path) AS count
                    FROM email_deliveries
                    WHERE status='sent'
                    """
                ).fetchone()["count"]
            )
            ready_to_email = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM generated_documents gd
                    WHERE gd.status='generated'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM email_deliveries ed
                        WHERE ed.status='sent'
                          AND ed.output_path=gd.output_pdf
                      )
                    """
                ).fetchone()["count"]
            )
        finally:
            conn.close()
    else:
        output_dir = MEETING_DIR / "output"
        if output_dir.exists():
            total_outputs = len(
                [path for path in output_dir.glob("*") if path.is_file() and path.suffix.lower() in {".pdf", ".zip"}]
            )
        ready_to_email = total_outputs
    return {
        "pendingReceiptCount": len(pending),
        "readyToEmailCount": ready_to_email,
        "emailedCount": emailed,
        "outputCount": total_outputs,
    }


def api_path(path: Path) -> str:
    try:
        return "/" + repo_relative(path)
    except ValueError:
        return "/" + str(path)


def output_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    normalized = Path(meeting_db.normalize_output_path(path))
    return MEETING_DIR / normalized


def unsent_output_zips() -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    if meeting_db.MEETING_DB.exists():
        conn = meeting_db.connect()
        try:
            rows = conn.execute(
                """
                SELECT gd.output_pdf
                FROM generated_documents gd
                WHERE gd.status='generated'
                  AND lower(gd.output_pdf) LIKE '%.zip'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM email_deliveries ed
                    WHERE ed.status='sent'
                      AND ed.output_path=gd.output_pdf
                  )
                ORDER BY gd.generated_at, gd.output_pdf
                """
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            path = output_path(str(row["output_pdf"]))
            if path.exists() and path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(resolved)
        return paths

    output_dir = MEETING_DIR / "output"
    if not output_dir.exists():
        return []
    return [path.resolve() for path in sorted(output_dir.glob("*.zip")) if path.is_file()]


def receipt_path_from_values(file_name: str, receipt_path: str | None, archived_path: str | None) -> Path:
    for value in (receipt_path, archived_path):
        if value and Path(value).exists():
            return Path(value).resolve()
    for candidate in (MEETING_DIR / "receipt" / file_name, MEETING_DIR / "receipt" / "used" / file_name):
        if candidate.exists():
            return candidate.resolve()
    return MEETING_DIR / "receipt" / file_name


def meeting_items(limit: int = 200) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen_pending: set[str] = set()
    if meeting_db.MEETING_DB.exists():
        conn = meeting_db.connect()
        try:
            for row in conn.execute(
                """
                SELECT file_name, status, generated_at, receipt_path, archived_path, error
                FROM receipts
                WHERE status IN ('parsed', 'review', 'pending_trip', 'error')
                ORDER BY generated_at DESC, file_name
                """
            ):
                seen_pending.add(str(row["file_name"]))
                path = receipt_path_from_values(row["file_name"], row["receipt_path"], row["archived_path"])
                items.append(
                    {
                        "name": row["file_name"],
                        "path": api_path(path),
                        "status": "unprocessed",
                        "statusLabel": "Unprocessed",
                        "kind": "receipt",
                        "detail": " · ".join(
                            value
                            for value in (str(row["status"] or "unprocessed"), row["error"] or row["generated_at"] or "")
                            if value
                        ),
                        "updatedAt": "",
                    }
                )

            for row in conn.execute(
                """
                SELECT
                  gd.kind,
                  gd.output_pdf,
                  gd.topic,
                  gd.generated_at,
                  gd.total_price,
                  MAX(ed.sent_at) AS sent_at,
                  COUNT(ed.id) AS sent_count
                FROM generated_documents gd
                LEFT JOIN email_deliveries ed
                  ON ed.output_path=gd.output_pdf AND ed.status='sent'
                WHERE gd.status='generated'
                GROUP BY gd.id
                ORDER BY COALESCE(MAX(ed.sent_at), gd.generated_at) DESC, gd.output_pdf
                """
            ):
                path = output_path(row["output_pdf"])
                emailed = int(row["sent_count"] or 0) > 0
                items.append(
                    {
                        "name": path.name,
                        "path": api_path(path),
                        "status": "email-sent" if emailed else "processed",
                        "statusLabel": "Email sent" if emailed else "Processed",
                        "kind": row["kind"] or "document",
                        "detail": row["sent_at"] or row["generated_at"] or "",
                        "updatedAt": "",
                    }
                )
        finally:
            conn.close()

    receipt_dir = MEETING_DIR / "receipt"
    if receipt_dir.exists():
        for path in sorted(receipt_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if (
                path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() in MEETING_RECEIPT_SUFFIXES
                and path.name not in seen_pending
            ):
                items.append(
                    {
                        "name": path.name,
                        "path": api_path(path),
                        "status": "unprocessed",
                        "statusLabel": "Unprocessed",
                        "kind": "receipt",
                        "detail": "not processed",
                        "updatedAt": "",
                    }
                )
    return items[:limit]
