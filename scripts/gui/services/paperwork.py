from __future__ import annotations

import sys
from pathlib import Path

from .paths import ROOT_DIR, repo_relative


def process_purchase_command(
    case_dir: Path,
    project_id: str | None = None,
    parse_engine: str = "auto",
    inspection_date: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "scripts.paperwork.purchase.process_purchase",
        repo_relative(case_dir),
        "--parse-engine",
        parse_engine,
    ]
    if project_id:
        command.extend(["--project-id", project_id])
    if inspection_date:
        command.extend(["--inspection-date", inspection_date])
    return command


def process_receipts_command(
    receipt_paths: list[Path],
    continue_on_error: bool = True,
    allow_pending_trip: bool = True,
) -> list[str]:
    command = [sys.executable, "-m", "scripts.paperwork.meeting.process_receipts"]
    if continue_on_error:
        command.append("--continue-on-error")
    if allow_pending_trip:
        command.append("--allow-pending-trip")
    command.extend(repo_relative(path) for path in receipt_paths)
    return command


def send_meeting_mail_command(zip_path: Path, recipient: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "scripts.paperwork.meeting.email_zip",
        repo_relative(zip_path),
        "--to",
        recipient,
    ]


def portal_command(
    case_dirs: list[Path],
    project_id: str | None,
    step: str,
    headed: bool = False,
) -> list[str]:
    command = [sys.executable, "scripts/upload/gachon_portal_upload.py"]
    if project_id:
        command.extend(["--project-id", project_id])
    for case_dir in case_dirs:
        command.extend(["--case-dir", repo_relative(case_dir)])
    command.extend(["--step", step])
    if headed:
        command.append("--headed")
    return command


def command_cwd() -> Path:
    return ROOT_DIR
