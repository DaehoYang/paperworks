from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from .. import config, pdf_utils
from ..models import ReceiptRecord
from ..paths import OUTPUT_DIR, TRIP_TEMPLATE_PDF


SEOUL_TOKENS = ("서울", "서울역", "수서", "김포공항", "강남", "강서", "양재", "잠실")


def is_seoul(value: str) -> bool:
    return any(token in value for token in SEOUL_TOKENS)


def endpoint_key(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())


def non_seoul_endpoint(record: ReceiptRecord) -> str:
    if is_seoul(record.origin) and not is_seoul(record.destination):
        return endpoint_key(record.destination)
    if is_seoul(record.destination) and not is_seoul(record.origin):
        return endpoint_key(record.origin)
    return ""


def validate_pair(first: ReceiptRecord, second: ReceiptRecord) -> tuple[ReceiptRecord, ReceiptRecord]:
    outbound, inbound = sorted([first, second], key=lambda item: item.generated)
    if not is_seoul(outbound.origin):
        raise ValueError(f"outbound receipt must start from Seoul: {outbound.file_name} origin={outbound.origin!r}")
    if not is_seoul(inbound.destination):
        raise ValueError(f"return receipt must end in Seoul: {inbound.file_name} destination={inbound.destination!r}")
    if inbound.generated < outbound.generated:
        raise ValueError("return receipt is earlier than outbound receipt")
    if inbound.generated - outbound.generated > timedelta(days=1, hours=23, minutes=59):
        raise ValueError("return receipt must be same day or at latest the next day")
    if endpoint_key(outbound.destination) and endpoint_key(inbound.origin) and endpoint_key(outbound.destination) != endpoint_key(inbound.origin):
        raise ValueError(f"outbound destination and return origin do not match: {outbound.destination!r} != {inbound.origin!r}")
    return outbound, inbound


def find_pairs(records: list[ReceiptRecord]) -> list[tuple[ReceiptRecord, ReceiptRecord]]:
    transports = [record for record in records if record.receipt_type == "transport" and record.status in {"parsed", "pending_trip"}]
    pairs: list[tuple[ReceiptRecord, ReceiptRecord]] = []
    used: set[str] = set()
    for first in sorted(transports, key=lambda item: item.generated):
        if first.file_name in used:
            continue
        candidates: list[ReceiptRecord] = []
        for second in transports:
            if second.file_name == first.file_name or second.file_name in used:
                continue
            try:
                pair = validate_pair(first, second)
            except ValueError:
                continue
            if non_seoul_endpoint(pair[0]) and non_seoul_endpoint(pair[0]) == non_seoul_endpoint(pair[1]):
                candidates.append(second)
        if len(candidates) == 1:
            pair = validate_pair(first, candidates[0])
            pairs.append(pair)
            used.update([pair[0].file_name, pair[1].file_name])
    return pairs


def report_date_text(record: ReceiptRecord) -> str:
    return f"{record.generated.year}년 {record.generated.month:02d}월 {record.generated.day:02d}일"


def trip_output_zip(outbound: ReceiptRecord) -> Path:
    return OUTPUT_DIR / f"{outbound.generated:%y%m%d}_출장보고서.zip"


def generate(outbound: ReceiptRecord, inbound: ReceiptRecord, *, traveler: str | None = None, participation: str | None = None, birthdate: str | None = None, account: str | None = None, purpose: str = "공동연구 논의 및 연구 진행 상황 공유", result: str = "") -> tuple[ReceiptRecord, ReceiptRecord]:
    outbound, inbound = validate_pair(outbound, inbound)
    project = config.project_info()
    trip = config.trip_info()
    traveler = traveler or trip["traveler_name"]
    participation = participation or trip["participation"]
    birthdate = birthdate if birthdate is not None else trip["birthdate"]
    account = account if account is not None else trip["account"]
    result = result or "1. 출장 목적지 방문 및 관련 업무 수행\n2. 연구 진행 상황 공유 및 향후 일정 논의\n3. 출장 관련 자료 정리"
    values = {
        "과제번호": project.get("과제번호", ""),
        "지원기관": "가천대학교 산학협력단",
        "연구책임자소속": trip["principal_department"],
        "연구책임자성명": trip["principal_name"],
        "연구기간": project.get("연구기간", ""),
        "해당차수": "",
        "연구과제명": project.get("연구과제명", ""),
        "출장자성명": traveler,
        "참여구분": participation,
        "생년월일": birthdate,
        "지급계좌": account,
        "출장목적": purpose,
        "출장기간": f"{outbound.generated:%Y-%m-%d %H:%M} ~ {inbound.generated:%Y-%m-%d %H:%M}",
        "여비구분": "국내",
        "최종목적지": outbound.destination,
        "결과보고내용": result,
        "작성일자": report_date_text(inbound),
        "연구책임자서명": project.get("연구책임자", ""),
    }
    output_zip = trip_output_zip(outbound)
    with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        report_pdf = Path(handle.name)
    try:
        pdf_utils.fill_form(TRIP_TEMPLATE_PDF, report_pdf, values)
        pdf_utils.write_document_receipt_zip(report_pdf, [outbound.receipt_path, inbound.receipt_path], output_zip, "출장보고서.pdf")
    finally:
        report_pdf.unlink(missing_ok=True)
    pair_id = outbound.stem
    rel_pdf = str(output_zip.relative_to(OUTPUT_DIR.parents[0]))
    return (
        replace(outbound, status="generated", pair_id=pair_id, document_type="trip", output_pdf=rel_pdf),
        replace(inbound, status="generated", pair_id=pair_id, document_type="trip", output_pdf=rel_pdf),
    )
