#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import sys
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import db


WORKSPACE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CREDENTIALS = WORKSPACE_DIR / "credentials.json"
DEFAULT_TOKEN = WORKSPACE_DIR / "scripts" / "documents" / "token.json"
DEFAULT_RECIPIENT = "sheepvs5@gmail.com"
DEFAULT_SUBJECT = "바이오나노연구원 법인카드 사용내역"
DEFAULT_BODY = "안녕하세요. 물리학과 양대호입니다.\n법인카드 처리해주시면 감사하겠습니다."
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a meeting/trip zip package by Gmail.")
    parser.add_argument("zip_path", type=Path, help="Zip file to attach.")
    parser.add_argument("--to", default=DEFAULT_RECIPIENT)
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--body", default=DEFAULT_BODY)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--dry-run", action="store_true", help="Build the message but do not send it.")
    return parser.parse_args()


def gmail_service(credentials_path: Path, token_path: Path):
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(f"Gmail OAuth client file is missing: {credentials_path}")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(
                port=0,
                authorization_prompt_message="Open this URL to authorize Gmail send access:\n{url}\n",
            )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def build_message(*, to: str, subject: str, body: str, zip_path: Path) -> EmailMessage:
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    if zip_path.suffix.lower() != ".zip":
        raise ValueError(f"attachment must be a .zip file: {zip_path}")

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(
        zip_path.read_bytes(),
        maintype="application",
        subtype="zip",
        filename=zip_path.name,
    )
    return message


def encoded_message(message: EmailMessage) -> dict[str, str]:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return {"raw": raw}


def send_message(service, message: EmailMessage) -> dict:
    return service.users().messages().send(userId="me", body=encoded_message(message)).execute()


def main() -> None:
    args = parse_args()
    zip_path = args.zip_path if args.zip_path.is_absolute() else WORKSPACE_DIR / args.zip_path
    message = build_message(to=args.to, subject=args.subject, body=args.body, zip_path=zip_path)
    if args.dry_run:
        print(f"dry-run: to={args.to} subject={args.subject} attachment={zip_path}")
        return
    service = gmail_service(args.credentials, args.token)
    sent = send_message(service, message)
    db.mark_output_emailed(
        zip_path,
        recipient=args.to,
        subject=args.subject,
        gmail_message_id=sent.get("id"),
    )
    print(f"sent: id={sent.get('id')} attachment={zip_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
