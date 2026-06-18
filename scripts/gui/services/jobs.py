from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .paths import JOBS_DIR, ROOT_DIR, ensure_gui_dirs


@dataclass(frozen=True)
class Job:
    id: str
    dir: Path
    status: dict[str, object]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "-", value.strip())
    return value.strip("-")[:80] or "job"


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def load_job(job_id: str) -> Job:
    directory = job_dir(job_id)
    return Job(id=job_id, dir=directory, status=read_json(directory / "status.json"))


def list_jobs(limit: int = 100) -> list[Job]:
    ensure_gui_dirs()
    jobs: list[Job] = []
    for directory in sorted(JOBS_DIR.iterdir(), reverse=True):
        if directory.is_dir():
            jobs.append(Job(id=directory.name, dir=directory, status=read_json(directory / "status.json")))
    return jobs[:limit]


def start_job(kind: str, command: list[str], metadata: dict[str, object] | None = None, cwd: Path = ROOT_DIR) -> Job:
    ensure_gui_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_id = f"{stamp}-{slugify(kind)}-{uuid4().hex[:8]}"
    directory = job_dir(job_id)
    directory.mkdir(parents=True)
    status = {
        "id": job_id,
        "kind": kind,
        "state": "queued",
        "created_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "returncode": None,
    }
    if metadata:
        status.update(metadata)
    write_json(directory / "status.json", status)
    write_json(directory / "command.json", {"command": command, "cwd": str(cwd)})
    subprocess.Popen(
        [sys.executable, "-m", "scripts.gui.services.job_worker", job_id],
        cwd=str(ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return Job(id=job_id, dir=directory, status=status)


def read_log(job: Job, name: str, max_chars: int = 24000) -> str:
    path = job.dir / name
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def command_for_job(job: Job) -> list[str]:
    data = read_json(job.dir / "command.json")
    command = data.get("command")
    return [str(item) for item in command] if isinstance(command, list) else []
