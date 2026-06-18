#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents.db import (
    backfill_processed_sources_from_documents,
    connect,
    source_key_from_metadata,
    upsert_document,
    upsert_processed_source,
)


DEFAULT_DB = WORKSPACE_DIR / "purchase" / "documents.sqlite3"
DEFAULT_PURCHASE = WORKSPACE_DIR / "purchase"
DEFAULT_METADATA_ROOTS = [DEFAULT_PURCHASE, WORKSPACE_DIR / "documents" / "archive"]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(json_path: Path) -> dict | None:
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_purchase_pdf_index(purchase_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not purchase_root.exists():
        return result
    for path in sorted(purchase_root.rglob("*.pdf")):
        try:
            result.setdefault(file_sha256(path), path)
        except OSError:
            continue
    return result


def normalize_metadata_paths(
    metadata: dict,
    json_path: Path,
    *,
    purchase_root: Path,
    purchase_pdf_by_sha: dict[str, Path],
    include_unplaced: bool,
) -> dict | None:
    metadata_sha = metadata.get("sha256")
    candidates = [
        Path(value)
        for value in (metadata.get("file_path"), metadata.get("saved_pdf"))
        if isinstance(value, str) and value
    ]
    candidates.append(json_path.with_suffix(".pdf"))

    pdf_path = None
    for candidate in candidates:
        try:
            in_purchase = purchase_root.resolve() in candidate.resolve().parents
        except OSError:
            in_purchase = False
        if candidate.exists() and (include_unplaced or in_purchase):
            pdf_path = candidate
            break

    if not pdf_path:
        pdf_path = purchase_pdf_by_sha.get(metadata_sha) if metadata_sha else None
    if not pdf_path:
        return None
    if not pdf_path.exists():
        return None
    final_json_path = pdf_path.with_suffix(".json")
    metadata = dict(metadata)
    metadata["file_path"] = str(pdf_path)
    metadata["saved_pdf"] = str(pdf_path)
    metadata["json_path"] = str(final_json_path)
    metadata["sha256"] = metadata_sha or file_sha256(pdf_path)
    metadata["source_key"] = metadata.get("source_key") or source_key_from_metadata(metadata)
    return metadata


def backfill_from_json(
    roots: list[Path],
    db_path: Path,
    *,
    purchase_root: Path,
    include_unplaced: bool,
) -> tuple[int, int, int]:
    purchase_pdf_by_sha = build_purchase_pdf_index(purchase_root)
    conn = connect(db_path)
    scanned = 0
    restored_json = 0
    processed = 0
    try:
        for root in roots:
            for json_path in sorted(root.rglob("*.json")) if root.exists() else []:
                metadata = load_metadata(json_path)
                if not metadata:
                    continue
                metadata = normalize_metadata_paths(
                    metadata,
                    json_path,
                    purchase_root=purchase_root,
                    purchase_pdf_by_sha=purchase_pdf_by_sha,
                    include_unplaced=include_unplaced,
                )
                if not metadata:
                    continue
                scanned += 1
                final_json = Path(metadata["json_path"])
                if final_json != json_path or not final_json.exists():
                    final_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                    restored_json += 1
                document_id = upsert_document(conn, metadata)
                if source_key_from_metadata(metadata):
                    source_id = upsert_processed_source(
                        conn,
                        metadata,
                        document_id=document_id,
                        reason="backfill metadata json",
                    )
                    if source_id:
                        processed += 1
    finally:
        conn.close()
    return scanned, restored_json, processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill processed Gmail source records from existing purchase metadata.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--purchase-root", type=Path, default=DEFAULT_PURCHASE)
    parser.add_argument("--metadata-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--include-unplaced",
        action="store_true",
        help="Also index metadata whose PDF only exists outside purchase. By default these are skipped.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = connect(args.db)
    try:
        from_documents = backfill_processed_sources_from_documents(conn)
    finally:
        conn.close()
    metadata_roots = args.metadata_root or DEFAULT_METADATA_ROOTS
    scanned_json, restored_json, from_json = backfill_from_json(
        metadata_roots,
        args.db,
        purchase_root=args.purchase_root,
        include_unplaced=args.include_unplaced,
    )
    print(
        f"backfilled processed_sources: from_documents={from_documents} "
        f"json_documents={scanned_json} restored_json={restored_json} from_json={from_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
