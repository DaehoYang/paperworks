from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import automation as automation_services
from . import notifications as notification_services
from .paths import JOBS_DIR, ROOT_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return data


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_status(path: Path, **updates: object) -> None:
    status = read_json(path)
    status.update(updates)
    write_json(path, status)


def notify_automation_failure(status_path: Path, job_id: str, returncode: int, detail: str, stderr_path: Path | None = None) -> None:
    status = read_json(status_path)
    automation = status.get("automation")
    if not isinstance(automation, dict):
        return
    action = str(automation.get("action") or "")
    schedule = str(automation.get("schedule") or "")
    key = str(automation.get("key") or "")
    if not action or not schedule or not key:
        return
    stderr = ""
    if stderr_path and stderr_path.exists():
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    automation_services.record_run(action, schedule, key, ok=False, detail=detail)
    try:
        message_id = notification_services.send_automation_failure(
            action=action,
            schedule=schedule,
            key=key,
            detail=detail,
            job_id=job_id,
            returncode=returncode,
            stderr=stderr,
        )
        if message_id:
            update_status(status_path, notification_message_id=message_id)
    except Exception as exc:
        update_status(status_path, notification_error=str(exc))


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.gui.services.job_worker <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1]
    job_dir = JOBS_DIR / job_id
    status_path = job_dir / "status.json"
    command_data = read_json(job_dir / "command.json")
    command = command_data.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        update_status(status_path, state="failed", finished_at=utc_now(), returncode=2)
        notify_automation_failure(status_path, job_id, 2, "Invalid job command.")
        return 2
    cwd = Path(str(command_data.get("cwd") or ROOT_DIR))
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    update_status(status_path, state="running", started_at=utc_now())
    env = os.environ.copy()
    env.update(load_dotenv(ROOT_DIR / ".env"))
    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout, stderr_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as stderr:
        process = subprocess.Popen(command, cwd=str(cwd), stdout=stdout, stderr=stderr, text=True, env=env)
        update_status(status_path, pid=process.pid)
        returncode = process.wait()
    update_status(
        status_path,
        state="succeeded" if returncode == 0 else "failed",
        finished_at=utc_now(),
        returncode=returncode,
    )
    if returncode != 0:
        notify_automation_failure(status_path, job_id, returncode, f"Job failed with return code {returncode}.", stderr_path)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
