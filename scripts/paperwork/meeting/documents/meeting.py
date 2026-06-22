from __future__ import annotations

import csv
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

from .. import config, pdf_utils, records
from ..models import Member, ReceiptRecord
from ..paths import MINUTES_FORM_PDF, OUTPUT_DIR, SUMMARY_CSV


def choose_topic(record: ReceiptRecord, meeting_place: str, existing: list[ReceiptRecord]) -> str:
    external = bool(config.external_members_for_place(meeting_place))
    order = config.topic_order(external=external)
    used = [item.document_type for item in existing if item.document_type in order and item.file_name != record.file_name]
    if not used:
        return order[0]
    return order[(order.index(used[-1]) + 1) % len(order)]


def attendee_history(exclude_file_name: str = "") -> list[list[str]]:
    if not SUMMARY_CSV.exists():
        return []
    with SUMMARY_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [
            [name.strip() for name in (row.get("attendee_names") or "").split(";") if name.strip()]
            for row in rows
            if row.get("file_name") != exclude_file_name
        ]


def attendee_usage(history: list[list[str]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for names in history:
        for name in names:
            usage[name] = usage.get(name, 0) + 1
    return usage


def stable_member_order_key(record: ReceiptRecord, member: Member) -> str:
    raw = f"{record.generated:%Y%m%d%H%M%S}|{record.file_name}|{member.name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_student_position(position: str) -> bool:
    return "학생" in position or "학부생" in position


def select_from_tier(record: ReceiptRecord, tier: list[Member], slots: int, usage: dict[str, int]) -> list[Member]:
    selected: list[Member] = []
    remaining = tier[:]
    while remaining and len(selected) < slots:
        remaining.sort(key=lambda member: (usage.get(member.name, 0), stable_member_order_key(record, member)))
        member = remaining.pop(0)
        selected.append(member)
        usage[member.name] = usage.get(member.name, 0) + 1
    return selected


def select_attendees(record: ReceiptRecord, count: int, meeting_place: str, history: list[list[str]] | None = None) -> list[Member]:
    members = config.members()
    rules = config.attendee_rules()
    fixed_first = str(rules.get("fixed_first_attendee") or "양대호")
    lead = next((member for member in members if member.name == fixed_first), None)
    external = config.external_members_for_place(meeting_place)
    required = ([lead] if lead else []) + external
    required_names = {member.name for member in required}
    count = max(count, len(required))
    if count >= len(members):
        return (required + [member for member in members if member.name not in required_names])[:count]

    usage = attendee_usage(history if history is not None else attendee_history(record.file_name))
    candidates = [member for member in members if member.name not in required_names]
    graduate_students = [member for member in candidates if "대학원생" in member.position]
    other_students = [member for member in candidates if "대학원생" not in member.position and is_student_position(member.position)]
    others = [member for member in candidates if member not in graduate_students and member not in other_students]

    selected = required[:]
    for tier in (graduate_students, other_students, others):
        if len(selected) >= count:
            break
        selected.extend(select_from_tier(record, tier, count - len(selected), usage))
    return selected[:count]


def numbered_lines(lines: list[str]) -> str:
    return "\n".join(f"{idx}. {line}" for idx, line in enumerate(lines, 1))


def round_to_nearest_half_hour(value: datetime) -> datetime:
    base = value.replace(minute=0, second=0, microsecond=0)
    minutes = value.minute + value.second / 60 + value.microsecond / 60_000_000
    if minutes <= 15:
        return base
    if minutes <= 45:
        return base + timedelta(minutes=30)
    return base + timedelta(hours=1)


def meeting_time_range(receipt_time: datetime) -> tuple[datetime, datetime]:
    start = round_to_nearest_half_hour(receipt_time - timedelta(hours=1))
    end = start + timedelta(hours=1)
    while end < receipt_time:
        end += timedelta(minutes=30)
    return start, end


def meeting_time_text(receipt_time: datetime) -> str:
    start, end = meeting_time_range(receipt_time)
    if start.date() == end.date():
        return f"{start:%Y년 %m월 %d일 %H:%M} ~ {end:%H:%M}"
    return f"{start:%Y년 %m월 %d일 %H:%M} ~ {end:%Y년 %m월 %d일 %H:%M}"


def meeting_output_zip(record: ReceiptRecord, existing: list[ReceiptRecord]) -> Path:
    start, _end = meeting_time_range(record.generated)
    base_name = f"{start:%y%m%d_%H%M}_회의록"
    used = {
        OUTPUT_DIR.parents[0] / item.output_pdf
        for item in existing
        if item.file_name != record.file_name and item.output_pdf
    }
    candidate = OUTPUT_DIR / f"{base_name}.zip"
    if candidate not in used:
        return candidate
    index = 2
    while True:
        candidate = OUTPUT_DIR / f"{base_name}_{index}.zip"
        if candidate not in used:
            return candidate
        index += 1


def make_minutes_pdf(record: ReceiptRecord, topic: str, attendees: list[Member], meeting_place: str, output_pdf: Path) -> None:
    project = config.project_info()
    title, body_lines = config.all_topics()[topic]
    values = {
        "과제번호": project.get("과제번호", ""),
        "연구책임자": project.get("연구책임자", ""),
        "연구과제명": project.get("연구과제명", ""),
        "연구기간": project.get("연구기간", ""),
        "회의제목": title,
        "회의일시": meeting_time_text(record.generated),
        "회의장소": meeting_place,
        "회의내용": numbered_lines(body_lines),
    }
    for idx, member in enumerate(attendees, 1):
        values[f"소속{idx}"] = member.department
        values[f"직위{idx}"] = member.position
        values[f"성명{idx}"] = member.name

    reader = PdfReader(str(MINUTES_FORM_PDF))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(writer.pages[0], values, auto_regenerate=False)
    acroform = writer._root_object.get(NameObject("/AcroForm"))
    if acroform:
        acroform.get_object()[NameObject("/NeedAppearances")] = BooleanObject(True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def generate(record: ReceiptRecord, existing: list[ReceiptRecord]) -> ReceiptRecord:
    meeting_place = config.derive_meeting_place(record.address, record.store_name)
    topic = choose_topic(record, meeting_place, existing)
    count = config.attendee_count(record.total_price, record.item_count, record.food_count, record.drink_count, record.store_name)
    attendees = select_attendees(record, count, meeting_place)
    count = len(attendees)
    output_zip = meeting_output_zip(record, existing)
    legacy_minutes_pdf = OUTPUT_DIR / f"{record.stem}_바나연회의록.pdf"
    legacy_combined_pdf = OUTPUT_DIR / f"{record.stem}_바나연회의록_영수증첨부.pdf"
    with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        minutes_pdf = Path(handle.name)
    try:
        make_minutes_pdf(record, topic, attendees, meeting_place, minutes_pdf)
        pdf_utils.write_document_receipt_zip(minutes_pdf, [record.receipt_path], output_zip, "회의록.pdf")
    finally:
        minutes_pdf.unlink(missing_ok=True)
        legacy_minutes_pdf.unlink(missing_ok=True)
        if legacy_combined_pdf != output_zip:
            legacy_combined_pdf.unlink(missing_ok=True)
    records.update_summary(record, meeting_place, topic, count, ";".join(member.name for member in attendees))
    return replace(record, status="generated", document_type=topic, output_pdf=str(output_zip.relative_to(OUTPUT_DIR.parents[0])))
