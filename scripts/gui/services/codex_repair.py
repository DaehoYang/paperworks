from __future__ import annotations

import shutil
from pathlib import Path

from .jobs import Job, read_log, start_job
from .paths import ROOT_DIR


SECRET_NAMES = ("secret.json", "credentials.json", "token.json", ".env")


def build_safe_prompt(failed_job: Job) -> str:
    stdout = read_log(failed_job, "stdout.log", max_chars=12000)
    stderr = read_log(failed_job, "stderr.log", max_chars=12000)
    status = failed_job.status
    return f"""You are diagnosing a failed local paperwork automation job.

Rules:
- Do not modify files.
- Do not read or print secret files: {", ".join(SECRET_NAMES)}.
- Do not run portal upload steps that create external side effects.
- Focus on root cause, affected code path, and a concrete fix plan.
- If a code change is likely needed, describe the smallest patch.

Repository root: {ROOT_DIR}
Failed job id: {failed_job.id}
Failed job status:
{status}

STDOUT tail:
{stdout}

STDERR tail:
{stderr}
"""


def start_safe_analysis(failed_job: Job, codex_bin: str = "codex") -> Job:
    if not shutil.which(codex_bin):
        raise FileNotFoundError(f"Codex binary not found: {codex_bin}")
    prompt = build_safe_prompt(failed_job)
    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "--color",
        "never",
        prompt,
    ]
    return start_job(
        "codex-safe-analysis",
        command,
        metadata={"source_job_id": failed_job.id, "mode": "safe"},
        cwd=ROOT_DIR,
    )
