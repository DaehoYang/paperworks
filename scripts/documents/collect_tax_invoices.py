#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import email.utils
import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
WORK_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = WORKSPACE_DIR / "tax_invoices" / "archive"
DEFAULT_CREDENTIALS = WORKSPACE_DIR / "credentials.json"
DEFAULT_TOKEN = WORK_DIR / "token.json"
PROCESSED_LABEL = "TaxInvoice/processed"
UNPROCESSED_LABEL = "TaxInvoice/unprocessed"
FINISHED_LABEL = "TaxInvoice/finished"
OBSOLETE_LABELS = (
    "TaxInvoice/error",
    "TaxInvoice/manual",
    "Documents/processed",
    "Documents/error",
    "Documents/manual",
)
MANAGED_LABELS = (PROCESSED_LABEL, UNPROCESSED_LABEL, FINISHED_LABEL, *OBSOLETE_LABELS)

DEFAULT_QUERIES = [
    'in:anywhere -in:spam -in:trash newer_than:{newer_than} from:hometaxadmin@hometax.go.kr',
    'in:anywhere -in:spam -in:trash newer_than:{newer_than} "NTS_eTaxInvoice.html"',
    'in:anywhere -in:spam -in:trash newer_than:{newer_than} "전자세금계산서"',
    'in:anywhere -in:spam -in:trash newer_than:{newer_than} "전자(세금)계산서"',
    'in:anywhere -in:spam -in:trash newer_than:{newer_than} "세금계산서 발급"',
]

TAX_ATTACHMENT_EXTS = {".pdf", ".xml", ".html", ".htm"}
IMAGE_ATTACHMENT_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
ALLOWED_LINK_HOSTS = ("l.ecount.com", "hometax.go.kr", "srtk.hometax.go.kr")


@dataclass
class AttachmentPart:
    part_id: str
    filename: str
    mime_type: str
    attachment_id: str
    size: int


def gmail_service(credentials_path: Path, token_path: Path):
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth client file is missing: {credentials_path}. "
                    "Create an OAuth desktop client in Google Cloud and save it there."
                )
            flow = InstalledAppFlow.from_client_config(load_client_config(credentials_path), SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=False,
                authorization_prompt_message="Open this URL to authorize Gmail access:\n{url}\n",
            )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def load_client_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "installed" in raw or "web" in raw:
        return raw
    client_id = raw.get("client_id") or raw.get("ID") or raw.get("id")
    client_secret = raw.get("client_secret") or raw.get("secret")
    if not client_id or not client_secret:
        raise ValueError(
            f"{path} must be a Google OAuth client JSON, or contain ID/client_id and secret/client_secret."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def get_or_create_label(service, name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == name:
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"]


def existing_label_ids(service, names: Iterable[str]) -> dict[str, str]:
    wanted = set(names)
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {label["name"]: label["id"] for label in labels if label.get("name") in wanted}


def add_label(service, message_id: str, label_id: str) -> None:
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def set_managed_label(service, message_id: str, label_id: str, managed_label_ids: Iterable[str]) -> None:
    managed = set(managed_label_ids)
    remove_label_ids = set(managed - {label_id})
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "addLabelIds": [label_id],
            "removeLabelIds": sorted(remove_label_ids),
        },
    ).execute()


def search_message_ids(service, queries: Iterable[str], max_results: int | None = None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        page_token = None
        while True:
            response = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=100, pageToken=page_token)
                .execute()
            )
            for item in response.get("messages", []):
                message_id = item["id"]
                if message_id not in seen:
                    seen.add(message_id)
                    ordered.append(message_id)
                    if max_results and len(ordered) >= max_results:
                        return ordered
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    return ordered


def header(headers: list[dict[str, str]], name: str) -> str:
    lower = name.lower()
    for item in headers:
        if item.get("name", "").lower() == lower:
            return item.get("value", "")
    return ""


def iter_parts(payload: dict) -> Iterable[dict]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from iter_parts(part)


def attachments_from_message(message: dict) -> list[AttachmentPart]:
    result: list[AttachmentPart] = []
    for part in iter_parts(message.get("payload", {})):
        body = part.get("body", {}) or {}
        filename = part.get("filename") or ""
        attachment_id = body.get("attachmentId")
        if not filename or not attachment_id:
            continue
        result.append(
            AttachmentPart(
                part_id=part.get("partId") or attachment_id,
                filename=filename,
                mime_type=part.get("mimeType") or "application/octet-stream",
                attachment_id=attachment_id,
                size=int(body.get("size") or 0),
            )
        )
    return result


def attachment_is_candidate(part: AttachmentPart, from_: str, subject: str) -> bool:
    suffix = Path(part.filename).suffix.lower()
    filename = part.filename.lower()
    from_lower = from_.lower()
    subject_text = subject.lower()
    if "hometax" in from_lower and suffix in {".html", ".htm"}:
        return True
    if "ecount" in from_lower and suffix in TAX_ATTACHMENT_EXTS:
        return True
    explicit_tokens = (
        "nts_etaxinvoice",
        "etaxinvoice",
        "taxinvoice",
        "전자세금",
        "전자(세금)",
        "세금계산서",
    )
    if suffix in TAX_ATTACHMENT_EXTS and any(token in filename for token in explicit_tokens):
        return True
    if suffix in {".xml", ".html", ".htm"} and any(token in subject_text for token in ("전자세금", "전자(세금)")):
        return True
    return False


def decode_attachment(service, message_id: str, attachment_id: str) -> bytes:
    data = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
        .get("data", "")
    )
    return base64.urlsafe_b64decode(data.encode("ascii"))


def safe_name(value: str, fallback: str = "invoice") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:80] or fallback


def parsed_email_datetime(value: str) -> datetime:
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def infer_vendor(subject: str, body_text: str) -> str:
    for text in (subject, body_text):
        match = re.search(r"\(([^()]+?)->", text)
        if match:
            return match.group(1).strip()
        match = re.search(r"이용하여\s+(.+?)\s+사업자가", text)
        if match:
            return match.group(1).strip()
        match = re.search(r"보낸회사\s+(.+?)(?:\n|발행일자)", text, flags=re.DOTALL)
        if match:
            return " ".join(match.group(1).split())
        match = re.search(r"\[([^\]]+)\].*전자\(세금\)계산서", text)
        if match:
            return match.group(1).strip()
    return "unknown_vendor"


def infer_issue_date(body_text: str, email_dt: datetime) -> str:
    patterns = [
        r"발급일자\s*:\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        r"발행일자\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, body_text)
        if match:
            y, m, d = map(int, match.groups())
            return f"{y:04d}-{m:02d}-{d:02d}"
    return email_dt.strftime("%Y-%m-%d")


def body_text_from_payload(payload: dict) -> str:
    texts: list[str] = []
    for part in iter_parts(payload):
        mime_type = part.get("mimeType") or ""
        data = (part.get("body") or {}).get("data")
        if not data or mime_type not in {"text/plain", "text/html"}:
            continue
        raw = base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
        if mime_type == "text/html":
            raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
            raw = re.sub(r"<[^>]+>", " ", raw)
            raw = html.unescape(raw)
        texts.append(raw)
    return "\n".join(texts)


def body_html_from_payload(payload: dict) -> str:
    chunks: list[str] = []
    for part in iter_parts(payload):
        if part.get("mimeType") != "text/html":
            continue
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        chunks.append(base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace"))
    return "\n".join(chunks)


def allowed_invoice_links(body_html: str, body_text: str) -> list[str]:
    urls = set(re.findall(r"https?://[^\s'\"<>]+", body_html + "\n" + body_text))
    for href in re.findall(r"""href=["']([^"']+)["']""", body_html, flags=re.I):
        if href.startswith("http://") or href.startswith("https://"):
            urls.add(html.unescape(href))
    result: list[str] = []
    for url in sorted(urls):
        if Path(urlparse(url).path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"}:
            continue
        host = urlparse(url).netloc.lower()
        if any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_LINK_HOSTS):
            result.append(url)
    return result


def write_metadata(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def output_stem(issue_date: str, vendor: str, message_id: str, index: int | None = None) -> str:
    yyyymmdd = issue_date.replace("-", "")[2:]
    stem = f"{yyyymmdd}_{safe_name(vendor)}_{message_id}"
    if index and index > 1:
        stem += f"_{index}"
    return stem


def html_to_pdf(source: Path, target: Path, hometax_password: str | None = None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(source.resolve().as_uri(), wait_until="networkidle")
        password_input = page.locator("input[type=password]").first
        if password_input.count() > 0 and password_input.is_visible():
            if not hometax_password:
                browser.close()
                raise RuntimeError("HTML invoice requires a password; set HOMETAX_PASSWORD or pass --hometax-password.")
            password_input.fill(hometax_password)
            page.get_by_role("button", name=re.compile("확인|OK|Confirm")).first.click()
            page.wait_for_timeout(1500)
        page.emulate_media(media="screen")
        target.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(path=str(target), format="A4", print_background=True)
        browser.close()


def xml_to_pdf(source: Path, target: Path) -> None:
    from playwright.sync_api import sync_playwright

    text = source.read_text(encoding="utf-8", errors="replace")
    pretty = html.escape(text)
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "xml.html"
        html_path.write_text(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>body{font-family:monospace;font-size:10px;white-space:pre-wrap}</style>"
            "</head><body><pre>"
            + pretty
            + "</pre></body></html>",
            encoding="utf-8",
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            target.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(target), format="A4", print_background=True)
            browser.close()


def link_to_pdf(url: str, target: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.emulate_media(media="screen")
        target.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(path=str(target), format="A4", print_background=True)
        browser.close()


def image_to_pdf(source: Path, target: Path) -> None:
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(source))
    if image.mode in {"RGBA", "LA", "P"}:
        image = image.convert("RGB")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PDF", resolution=100.0)


def convert_to_pdf(source: Path, target: Path, hometax_password: str | None) -> None:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        target.write_bytes(source.read_bytes())
    elif suffix in {".html", ".htm"}:
        html_to_pdf(source, target, hometax_password=hometax_password)
    elif suffix == ".xml":
        xml_to_pdf(source, target)
    elif suffix in IMAGE_ATTACHMENT_EXTS:
        image_to_pdf(source, target)
    else:
        raise RuntimeError(f"Unsupported tax invoice attachment type: {source.name}")


def collect(args: argparse.Namespace) -> int:
    service = gmail_service(args.credentials, args.token)
    existing_managed = existing_label_ids(service, MANAGED_LABELS)
    labels = {
        "ok": get_or_create_label(service, PROCESSED_LABEL),
        "unprocessed": get_or_create_label(service, UNPROCESSED_LABEL),
    }
    managed_label_ids = set(existing_managed.values()) | set(labels.values())
    queries = [q.format(newer_than=args.newer_than) for q in DEFAULT_QUERIES]
    message_ids = search_message_ids(service, queries, max_results=args.max_messages)
    print(f"Found {len(message_ids)} candidate messages")

    ok_count = 0
    error_count = 0
    manual_count = 0
    for message_id in message_ids:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        subject = header(headers, "Subject")
        from_ = header(headers, "From")
        date_value = header(headers, "Date")
        email_dt = parsed_email_datetime(date_value) if date_value else datetime.now().astimezone()
        body_text = body_text_from_payload(payload)
        body_html = body_html_from_payload(payload)
        issue_date = infer_issue_date(body_text, email_dt)
        vendor = infer_vendor(subject, body_text)
        parts = [p for p in attachments_from_message(message) if attachment_is_candidate(p, from_, subject)]
        links = allowed_invoice_links(body_html, body_text)

        if not parts:
            if links:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                message_had_error = False
                for index, url in enumerate(links, start=1):
                    stem = output_stem(issue_date, vendor, message_id, index if len(links) > 1 else None)
                    pdf_path = args.output_dir / f"{stem}.pdf"
                    metadata_path = args.output_dir / f"{stem}.json"
                    if pdf_path.exists() and metadata_path.exists() and not args.force:
                        continue
                    try:
                        link_to_pdf(url, pdf_path)
                        write_metadata(
                            metadata_path,
                            {
                                "message_id": message_id,
                                "thread_id": message.get("threadId"),
                                "from": from_,
                                "subject": subject,
                                "email_date": email_dt.isoformat(),
                                "issue_date": issue_date,
                                "vendor": vendor,
                                "gmail_url": f"https://mail.google.com/mail/#all/{message_id}",
                                "source_type": "link",
                                "source_link": url,
                                "saved_pdf": str(pdf_path),
                                "saved_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        ok_count += 1
                        print(f"ok: {pdf_path}")
                    except Exception as exc:
                        message_had_error = True
                        error_count += 1
                        print(f"error: {message_id} link {url}: {exc}")
                set_managed_label(service, message_id, labels["unprocessed" if message_had_error else "ok"], managed_label_ids)
                continue
            else:
                set_managed_label(service, message_id, labels["unprocessed"], managed_label_ids)
                manual_count += 1
                print(f"manual: {message_id} has no supported attachment: {subject}")
                continue

        args.output_dir.mkdir(parents=True, exist_ok=True)
        message_had_error = False
        for index, part in enumerate(parts, start=1):
            stem = output_stem(issue_date, vendor, message_id, index if len(parts) > 1 else None)
            pdf_path = args.output_dir / f"{stem}.pdf"
            metadata_path = args.output_dir / f"{stem}.json"
            if pdf_path.exists() and metadata_path.exists() and not args.force:
                continue
            try:
                data = decode_attachment(service, message_id, part.attachment_id)
                digest = hashlib.sha256(data).hexdigest()
                with tempfile.TemporaryDirectory() as td:
                    source_path = Path(td) / part.filename
                    source_path.write_bytes(data)
                    convert_to_pdf(source_path, pdf_path, hometax_password=args.hometax_password)
                write_metadata(
                    metadata_path,
                    {
                        "message_id": message_id,
                        "thread_id": message.get("threadId"),
                        "from": from_,
                        "subject": subject,
                        "email_date": email_dt.isoformat(),
                        "issue_date": issue_date,
                        "vendor": vendor,
                        "gmail_url": f"https://mail.google.com/mail/#all/{message_id}",
                        "source_type": "attachment",
                        "source_filename": part.filename,
                        "source_mime_type": part.mime_type,
                        "source_size": part.size,
                        "source_sha256": digest,
                        "saved_pdf": str(pdf_path),
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                ok_count += 1
                print(f"ok: {pdf_path}")
            except Exception as exc:
                message_had_error = True
                error_count += 1
                print(f"error: {message_id} {part.filename}: {exc}")
        set_managed_label(service, message_id, labels["unprocessed" if message_had_error else "ok"], managed_label_ids)

    print(f"Done: ok={ok_count}, manual={manual_count}, errors={error_count}")
    return 1 if error_count else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Gmail electronic tax invoices and save every invoice as PDF.")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--newer-than", default="1d", help="Gmail newer_than value, e.g. 1d, 14d, 2m.")
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--force", action="store_true", help="Overwrite existing PDF and JSON files.")
    parser.add_argument(
        "--hometax-password",
        default=os.environ.get("HOMETAX_PASSWORD"),
        help="HomeTax secure mail password, usually recipient business number. Defaults to HOMETAX_PASSWORD.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
