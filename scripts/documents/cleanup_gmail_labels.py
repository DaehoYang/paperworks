#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from scripts.documents import collect_tax_invoices as tax


DEFAULT_CREDENTIALS = WORKSPACE_DIR / "credentials.json"
DEFAULT_TOKEN = Path(__file__).resolve().parent / "token.json"


def label_map(service) -> dict[str, str]:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {label["name"]: label["id"] for label in labels}


def message_ids_for_label(service, label_id: str, max_messages: int | None = None) -> list[str]:
    result: list[str] = []
    page_token = None
    while True:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=[label_id],
                pageToken=page_token,
                maxResults=min(500, max_messages - len(result)) if max_messages else 500,
            )
            .execute()
        )
        result.extend(message["id"] for message in response.get("messages", []))
        if max_messages and len(result) >= max_messages:
            return result[:max_messages]
        page_token = response.get("nextPageToken")
        if not page_token:
            return result


def cleanup_labels(*, dry_run: bool, keep_obsolete_labels: bool, max_messages: int | None) -> tuple[int, int]:
    service = tax.gmail_service(DEFAULT_CREDENTIALS, DEFAULT_TOKEN)
    labels = label_map(service)
    if dry_run:
        processed_id = labels.get(tax.PROCESSED_LABEL, tax.PROCESSED_LABEL)
        unprocessed_id = labels.get(tax.UNPROCESSED_LABEL, tax.UNPROCESSED_LABEL)
        finished_id = labels.get(tax.FINISHED_LABEL, tax.FINISHED_LABEL)
    else:
        processed_id = tax.get_or_create_label(service, tax.PROCESSED_LABEL)
        unprocessed_id = tax.get_or_create_label(service, tax.UNPROCESSED_LABEL)
        finished_id = tax.get_or_create_label(service, tax.FINISHED_LABEL)
        labels = label_map(service)

    managed_names = [tax.PROCESSED_LABEL, tax.UNPROCESSED_LABEL, *tax.OBSOLETE_LABELS]
    managed_names.append(tax.FINISHED_LABEL)
    managed_ids = {labels[name] for name in managed_names if name in labels} | {processed_id, unprocessed_id, finished_id}
    processed_names = {tax.PROCESSED_LABEL, "Documents/processed"}

    message_labels: dict[str, set[str]] = {}
    for name in managed_names:
        label_id = labels.get(name)
        if not label_id:
            continue
        for message_id in message_ids_for_label(service, label_id, max_messages=max_messages):
            message_labels.setdefault(message_id, set()).add(name)

    changed = 0
    for message_id, names in sorted(message_labels.items()):
        if tax.FINISHED_LABEL in names:
            target_id = finished_id
            target_name = tax.FINISHED_LABEL
        elif names & processed_names:
            target_id = processed_id
            target_name = tax.PROCESSED_LABEL
        else:
            target_id = unprocessed_id
            target_name = tax.UNPROCESSED_LABEL
        print(f"{'dry-run: ' if dry_run else ''}{message_id}: {sorted(names)} -> {target_name}")
        if not dry_run:
            tax.set_managed_label(service, message_id, target_id, managed_ids)
        changed += 1

    deleted = 0
    if not dry_run and not keep_obsolete_labels:
        labels = label_map(service)
        for name in tax.OBSOLETE_LABELS:
            label_id = labels.get(name)
            if not label_id:
                continue
            service.users().labels().delete(userId="me", id=label_id).execute()
            print(f"deleted label: {name}")
            deleted += 1

    return changed, deleted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize Gmail tax invoice labels to processed/unprocessed.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-obsolete-labels", action="store_true")
    parser.add_argument("--max-messages", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed, deleted = cleanup_labels(
        dry_run=args.dry_run,
        keep_obsolete_labels=args.keep_obsolete_labels,
        max_messages=args.max_messages,
    )
    print(f"Done: normalized={changed}, deleted_obsolete_labels={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
