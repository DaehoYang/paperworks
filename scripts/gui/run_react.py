#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT_DIR / "scripts" / "gui" / "jobs" / "react-gui.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the React/FastAPI paperwork GUI.")
    parser.add_argument("--port", type=int, default=45001)
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--user", default=os.environ.get("JUPYTERHUB_USER") or os.environ.get("USER") or "sheepvs5")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "scripts.gui.react_backend.app:app",
        "--host",
        args.address,
        "--port",
        str(args.port),
    ]
    if args.reload:
        cmd.append("--reload")
    return cmd


def main() -> int:
    args = parse_args()
    cmd = command(args)
    if args.detach:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        log = args.log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        print(process.pid)
        print(f"https://dhlab.gachon.ac.kr/user/{args.user}/proxy/{args.port}/")
        print(f"log: {args.log}")
        return 0
    os.chdir(ROOT_DIR)
    os.execvp(cmd[0], cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
