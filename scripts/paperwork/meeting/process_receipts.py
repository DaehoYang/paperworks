#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import records, receipt_ocr
from . import validation
from .documents import meeting, trip
from .models import ReceiptRecord
from .paths import RECORDS_CSV, USED_RECEIPT_DIR


logging.getLogger("pypdf").setLevel(logging.ERROR)


MEETING_TYPES = {"food_drink", "restaurant", "cafe", "meal", "drink"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR receipts, route them, and generate meeting/trip PDFs.")
    parser.add_argument("receipts", nargs="*", help="Receipt files relative to meeting/receipt or absolute.")
    parser.add_argument("--ocr-engine", choices=["auto", "codex", "ocr-api-litellm"], default="auto")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--ocr-model")
    parser.add_argument("--ocr-timeout", type=int, default=180)
    parser.add_argument("--ocr-api-url", default=os.environ.get("DHLAB_OCR_API_URL", "https://dhlab.gachon.ac.kr/services/rag/ocr"))
    parser.add_argument("--ocr-api-key", default=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY"))
    parser.add_argument("--litellm-base-url", default=os.environ.get("DHLAB_LITELLM_BASE_URL", "https://dhlab.gachon.ac.kr/services/litellm/v1"))
    parser.add_argument("--litellm-api-key", default=os.environ.get("DHLAB_LITELLM_API_KEY"))
    parser.add_argument("--litellm-model", default=os.environ.get("DHLAB_LITELLM_MODEL", "local"))
    parser.add_argument("--metadata-json", help="JSON file/list of pre-parsed receipt records for tests or manual ingestion.")
    parser.add_argument("--traveler", help="Override trip traveler name from information.yml.")
    parser.add_argument("--participation", help="Override trip participation from information.yml.")
    parser.add_argument("--birthdate", help="Override trip traveler birthdate from information.yml.")
    parser.add_argument("--account", help="Override trip traveler account from information.yml.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and route without writing records or PDFs.")
    parser.add_argument("--continue-on-error", action="store_true", help="Record failed OCR/parse files as review and continue.")
    parser.add_argument("--allow-pending-trip", action="store_true", help="Do not exit with an error when a transport receipt has no pair.")
    parser.add_argument("--no-archive-receipts", action="store_true", help="Do not move processed input receipts to receipt/used.")
    return parser.parse_args()


def load_metadata_json(path: Path) -> list[ReceiptRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("--metadata-json must contain an object or list")
    return [receipt_ocr.record_from_json(item) for item in data if isinstance(item, dict)]


def parse_receipts(args: argparse.Namespace) -> list[ReceiptRecord]:
    parsed: list[ReceiptRecord] = []
    if args.metadata_json:
        parsed.extend(load_metadata_json(Path(args.metadata_json)))
    for receipt in args.receipts:
        print(f"parse: {Path(receipt).name}", flush=True)
        try:
            parsed.append(
                receipt_ocr.parse_receipt(
                    receipt,
                    ocr_engine=args.ocr_engine,
                    codex_bin=args.codex_bin,
                    ocr_model=args.ocr_model,
                    ocr_timeout=args.ocr_timeout,
                    ocr_api_url=args.ocr_api_url,
                    ocr_api_key=args.ocr_api_key,
                    litellm_base_url=args.litellm_base_url,
                    litellm_api_key=args.litellm_api_key,
                    litellm_model=args.litellm_model,
                )
            )
        except Exception as exc:
            if not args.continue_on_error:
                raise
            receipt_path = records.resolve_receipt_path(receipt)
            print(f"parse failed: {receipt_path.name}: {exc}", flush=True)
            parsed.append(
                ReceiptRecord(
                    file_name=receipt_path.name,
                    receipt_path=receipt_path,
                    generated=datetime.fromtimestamp(receipt_path.stat().st_mtime),
                    total_price=0,
                    receipt_type="unknown",
                    status="review",
                    error=f"parse_failed: {exc}",
                )
            )
    return parsed


def route_records(new_records: list[ReceiptRecord], existing: list[ReceiptRecord], args: argparse.Namespace) -> list[ReceiptRecord]:
    all_records = existing + [record for record in new_records if record.file_name not in {item.file_name for item in existing}]
    updated: dict[str, ReceiptRecord] = {record.file_name: record for record in all_records}

    for record in new_records:
        if record.status == "review":
            updated[record.file_name] = record
            print(f"review: {record.file_name} ({record.error or record.receipt_type})")
        elif record.receipt_type in MEETING_TYPES:
            result = validation.validate_meeting_generation_input(record)
            if not result.ok:
                reviewed = replace(record, status="review", error=f"pre_generate_validation_failed: {validation.format_errors(result)}")
                updated[record.file_name] = reviewed
                print(f"review: {record.file_name} ({reviewed.error})")
                continue
            if args.dry_run:
                preview = replace(record, status="would_generate", document_type="meeting")
                updated[record.file_name] = preview
                print(f"meeting: {record.file_name} -> dry-run")
            else:
                generated = meeting.generate(record, list(updated.values()))
                result = validation.validate_generated_records([generated])
                if not result.ok:
                    generated = replace(generated, status="error", error=f"post_generate_validation_failed: {validation.format_errors(result)}")
                updated[record.file_name] = generated
                print(f"meeting: {record.file_name} -> {generated.output_pdf}")
        elif record.receipt_type == "transport":
            pending = replace(record, status="pending_trip")
            updated[record.file_name] = pending
            print(f"transport pending: {record.file_name}")
        else:
            skipped = replace(record, status="review", error=f"unsupported receipt_type={record.receipt_type}")
            updated[record.file_name] = skipped
            print(f"review: {record.file_name} ({record.receipt_type})")

    pairs = trip.find_pairs(list(updated.values()))
    for outbound, inbound in pairs:
        if updated[outbound.file_name].status == "generated" or updated[inbound.file_name].status == "generated":
            continue
        if args.dry_run:
            updated[outbound.file_name] = replace(updated[outbound.file_name], status="would_generate", document_type="trip", pair_id=outbound.stem)
            updated[inbound.file_name] = replace(updated[inbound.file_name], status="would_generate", document_type="trip", pair_id=outbound.stem)
            print(f"trip: {outbound.file_name} + {inbound.file_name} -> dry-run")
        else:
            result = validation.validate_trip_generation_input(updated[outbound.file_name], updated[inbound.file_name])
            if not result.ok:
                message = f"pre_generate_validation_failed: {validation.format_errors(result)}"
                updated[outbound.file_name] = replace(updated[outbound.file_name], status="review", error=message)
                updated[inbound.file_name] = replace(updated[inbound.file_name], status="review", error=message)
                print(f"review: {outbound.file_name} + {inbound.file_name} ({message})")
                continue
            generated_out, generated_in = trip.generate(
                updated[outbound.file_name],
                updated[inbound.file_name],
                traveler=args.traveler,
                participation=args.participation,
                birthdate=args.birthdate,
                account=args.account,
            )
            result = validation.validate_generated_records([generated_out, generated_in])
            if not result.ok:
                message = f"post_generate_validation_failed: {validation.format_errors(result)}"
                generated_out = replace(generated_out, status="error", error=message)
                generated_in = replace(generated_in, status="error", error=message)
            updated[generated_out.file_name] = generated_out
            updated[generated_in.file_name] = generated_in
            print(f"trip: {generated_out.file_name} + {generated_in.file_name} -> {generated_out.output_pdf}")

    return sorted(updated.values(), key=lambda item: (item.generated, item.file_name))


def unique_archive_path(path: Path, stem: str | None = None) -> Path:
    base_stem = stem or path.stem
    suffix = path.suffix
    candidate = USED_RECEIPT_DIR / f"{base_stem}{suffix}"
    if not candidate.exists():
        return candidate
    if candidate.resolve() == path.resolve():
        return candidate
    index = 2
    while True:
        candidate = USED_RECEIPT_DIR / f"{base_stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        if candidate.resolve() == path.resolve():
            return candidate
        index += 1


def archive_stems(records_to_archive: list[ReceiptRecord]) -> dict[str, str]:
    output_counts: dict[str, int] = {}
    output_indexes: dict[str, int] = {}
    for record in records_to_archive:
        if record.output_pdf:
            output_counts[record.output_pdf] = output_counts.get(record.output_pdf, 0) + 1

    stems: dict[str, str] = {}
    for record in records_to_archive:
        if record.output_pdf:
            stem = Path(record.output_pdf).stem
            if output_counts.get(record.output_pdf, 0) > 1:
                output_indexes[record.output_pdf] = output_indexes.get(record.output_pdf, 0) + 1
                stem = f"{stem}_{output_indexes[record.output_pdf]}"
        else:
            stem = record.receipt_path.stem
        stems[record.file_name] = stem
    return stems


def archive_processed_receipts(records_to_archive: list[ReceiptRecord]) -> dict[str, Path]:
    USED_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = set()
    archived: dict[str, Path] = {}
    stems = archive_stems(records_to_archive)
    for record in records_to_archive:
        source = record.receipt_path.resolve()
        if source in seen or not source.exists():
            continue
        seen.add(source)
        if source.parent.resolve() == USED_RECEIPT_DIR.resolve():
            continue
        destination = unique_archive_path(source, stems.get(record.file_name))
        source.rename(destination)
        archived[record.file_name] = destination
        print(f"archived: {source.name} -> {destination.relative_to(USED_RECEIPT_DIR.parent)}")
    return archived


def main() -> None:
    args = parse_args()
    new_records = parse_receipts(args)
    existing = records.read_records()
    routed = route_records(new_records, existing, args)
    if not args.dry_run:
        records.write_records(routed)
        if not args.no_archive_receipts:
            new_names = {record.file_name for record in new_records}
            archived_paths = archive_processed_receipts(
                [record for record in routed if record.file_name in new_names and record.status == "generated"]
            )
            if archived_paths:
                routed = [
                    replace(record, receipt_path=archived_paths.get(record.file_name, record.receipt_path))
                    for record in routed
                ]
                records.write_records(routed)
    new_names = {record.file_name for record in new_records}
    pending = [record for record in routed if record.status == "pending_trip" and record.file_name in new_names]
    if pending and not args.allow_pending_trip:
        names = ", ".join(record.file_name for record in pending)
        print(f"records_csv: {RECORDS_CSV}")
        raise SystemExit(f"error: could not find matching trip pair for: {names}")
    print(f"records_csv: {RECORDS_CSV}")


if __name__ == "__main__":
    main()
