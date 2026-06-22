from __future__ import annotations

from email.message import EmailMessage

from . import automation as automation_services


def tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def notification_recipient() -> str:
    settings = automation_services.read_settings()
    return str(settings.get("notificationEmailRecipient") or "").strip()


def send_automation_failure(
    *,
    action: str,
    schedule: str,
    key: str,
    detail: str,
    job_id: str | None = None,
    returncode: int | None = None,
    stderr: str = "",
) -> str | None:
    recipient = notification_recipient()
    if not recipient:
        return None

    subject = f"Paperworks automation failed: {automation_services.ACTION_LABELS.get(action, action)}"
    lines = [
        "Paperworks automation failed.",
        "",
        f"Action: {automation_services.ACTION_LABELS.get(action, action)}",
        f"Schedule: {schedule}",
        f"Key: {key}",
    ]
    if job_id:
        lines.append(f"Job: {job_id}")
    if returncode is not None:
        lines.append(f"Return code: {returncode}")
    if detail:
        lines.extend(["", "Detail:", tail(detail)])
    if stderr:
        lines.extend(["", "stderr:", tail(stderr)])

    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content("\n".join(lines))

    from scripts.paperwork.meeting import email_zip

    service = email_zip.gmail_service(email_zip.DEFAULT_CREDENTIALS, email_zip.DEFAULT_TOKEN)
    sent = email_zip.send_message(service, message)
    sent_id = sent.get("id")
    return str(sent_id) if sent_id else None
