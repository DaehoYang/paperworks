#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT_DIR / "scripts" / "gui" / "jobs" / "streamlit.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the paperwork Streamlit GUI with jupyter-server-proxy settings.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--user", default=os.environ.get("JUPYTERHUB_USER") or os.environ.get("USER") or "sheepvs5")
    parser.add_argument(
        "--base-url-path",
        help=(
            "Optional Streamlit server.baseUrlPath without a leading slash. "
            "Do not set this for the usual jupyter-server-proxy mode that strips /user/<name>/proxy/<port>."
        ),
    )
    parser.add_argument("--detach", action="store_true", help="Run in the background and write logs to --log.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    return parser.parse_args()


def streamlit_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "scripts/gui/app.py",
        "--server.address",
        args.address,
        "--server.port",
        str(args.port),
        "--server.headless",
        "true",
    ]
    if args.base_url_path:
        command.extend(["--server.baseUrlPath", args.base_url_path])
    return command


def main() -> int:
    args = parse_args()
    command = streamlit_command(args)
    if args.detach:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        log = args.log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
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
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
