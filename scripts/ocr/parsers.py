from __future__ import annotations

import re


MONEY_RE = re.compile(r"(?:₩\s*)?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{5,})")
COMMA_MONEY_RE = re.compile(r"(?:₩\s*)?([0-9]{1,3}(?:,[0-9]{3})+)")
DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[-./년]?\s*(?P<month>\d{1,2})\s*[-./월]?\s*(?P<day>\d{1,2})"
    r"(?:[일\s']+(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})(?:\s*:\s*(?P<second>\d{2}))?)?"
)
DATE_TIME_RE = re.compile(r"(20\d{2})\s*[-./년]?\s*(\d{1,2})\s*[-./월]?\s*(\d{1,2})\s*[일\s']*(\d{1,2})\s*:\s*(\d{2})(?:\s*:\s*(\d{2}))?")
BUSINESS_NO_RE = re.compile(r"\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{5})\b")
APPROVAL_RE = re.compile(r"승인[:\s;]*([0-9]{6,12})")
CARD_RE = re.compile(r"(\d{4}[-\s]\d{4}[-\s][*0-9]{3,4}[-\s][*0-9]{3,4})")
ACCOUNT_RE = re.compile(r"\b(\d{2,6})\s*[-]\s*(\d{2,6})\s*[-]\s*(\d{2,6})\s*[-]\s*(\d{2,6})\b")


def parse_text(doc_type: str, text: str) -> dict[str, object]:
    base: dict[str, object] = {"raw_text_length": len(text)}
    if doc_type == "receipt":
        base.update(parse_receipt(text))
    elif doc_type == "bankbook_copy":
        base.update(parse_bankbook(text))
    elif doc_type == "tax_invoice":
        base.update(parse_tax_invoice(text))
    elif doc_type == "business_registration":
        base.update(parse_business_registration(text))
    elif doc_type in {"estimate", "statement"}:
        base.update(parse_purchase_document(text))
    else:
        base.update(parse_generic(text))
    return base


def parse_generic(text: str) -> dict[str, object]:
    return {
        "text_present": bool(text.strip()),
        "issue_date": first_date(text),
        "amount": max_money(text),
    }


def parse_receipt(text: str) -> dict[str, object]:
    lines = nonempty_lines(text)
    return {
        "store_name": infer_receipt_store(lines),
        "generated": first_datetime(text),
        "total_price": receipt_total_price(text),
        "approval_number": first_group(APPROVAL_RE, text),
        "card_number": first_group(CARD_RE, text),
        "item_names": infer_receipt_items(lines),
    }


def parse_bankbook(text: str) -> dict[str, object]:
    lines = nonempty_lines(text)
    return {
        "bank_name": infer_bank_name(text),
        "account_holder": infer_after_label(lines, ("예금주", "예금주명", "Name")),
        "account_number": normalize_account(first_group(ACCOUNT_RE, text)),
        "issue_date": first_date(text),
    }


def parse_tax_invoice(text: str) -> dict[str, object]:
    return {
        "vendor": infer_vendor(text),
        "issue_date": first_date(text),
        "amount": tax_invoice_amount(text),
        "business_registration_number": business_number(text),
        "approval_number": infer_tax_approval(text),
    }


def parse_business_registration(text: str) -> dict[str, object]:
    return {
        "vendor": infer_vendor(text),
        "business_registration_number": business_number(text),
        "issue_date": first_date(text),
    }


def parse_purchase_document(text: str) -> dict[str, object]:
    return {
        "vendor": infer_vendor(text),
        "issue_date": first_date(text),
        "amount": max_money(text),
        "item_count": infer_item_count(text),
    }


def first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    if match.lastindex and match.lastindex > 1:
        return "-".join(match.groups())
    return match.group(1)


def normalize_account(value: str | None) -> str | None:
    if not value:
        return None
    groups = re.findall(r"\d{2,6}", value)
    return "-".join(groups) if len(groups) >= 3 else None


def first_date(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def first_datetime(text: str) -> str | None:
    time_match = DATE_TIME_RE.search(text)
    if time_match:
        year, month, day, hour, minute, second = time_match.groups(default="00")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}:{int(second):02d}"
    match = DATE_RE.search(text)
    if not match:
        return None
    date = f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    if match.group("hour") and match.group("minute"):
        second = int(match.group("second") or 0)
        return f"{date} {int(match.group('hour')):02d}:{int(match.group('minute')):02d}:{second:02d}"
    return date


def money_values(text: str) -> list[int]:
    values: list[int] = []
    for match in MONEY_RE.finditer(text):
        digits = re.sub(r"[^0-9]", "", match.group(1))
        if digits:
            values.append(int(digits))
    return values


def max_money(text: str) -> int | None:
    values = money_values(text)
    return max(values) if values else None


def receipt_total_price(text: str) -> int | None:
    candidates: list[int] = []
    labels = ("결제", "합계", "총", "일시불", "받은")
    lines = nonempty_lines(text)
    for idx, line in enumerate(lines):
        window = "\n".join(lines[max(0, idx - 3) : idx + 2])
        if any(label in window for label in labels):
            candidates.extend(value for value in comma_money_values(line) if 0 < value <= 10_000_000)
    if candidates:
        return max(candidates)
    values = [value for value in comma_money_values(text) if 0 < value <= 10_000_000]
    if values:
        return max(values)
    values = [value for value in money_values(text) if 0 < value <= 1_000_000]
    return max(values) if values else None


def tax_invoice_amount(text: str) -> int | None:
    lines = nonempty_lines(text)
    for idx, line in enumerate(lines):
        if "합계금액" in re.sub(r"\s+", "", line):
            window = "\n".join(lines[idx : idx + 5])
            values = [value for value in comma_money_values(window) if 1_000 <= value <= 100_000_000]
            if values:
                return max(values)

    for idx, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if "작성일자" in compact and "공급가액" in compact and "세액" in compact:
            for candidate_line in lines[idx + 1 : idx + 5]:
                values = [value for value in comma_money_values(candidate_line) if 1_000 <= value <= 100_000_000]
                if len(values) >= 2:
                    return values[0] + values[1]

    values = [value for value in comma_money_values(text) if 1_000 <= value <= 100_000_000]
    return max(values) if values else None


def comma_money_values(text: str) -> list[int]:
    values: list[int] = []
    for match in COMMA_MONEY_RE.finditer(text):
        digits = re.sub(r"[^0-9]", "", match.group(1))
        if digits:
            values.append(int(digits))
    return values


def business_number(text: str) -> str | None:
    match = BUSINESS_NO_RE.search(text)
    if not match:
        return None
    return "-".join(match.groups())


def nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def infer_receipt_store(lines: list[str]) -> str | None:
    skip = {"receipt", "영수증", "outlet", "newcore"}
    preferred: list[str] = []
    fallback: list[str] = []
    for line in lines[:12]:
        compact = re.sub(r"\s+", "", line)
        if len(compact) < 2 or compact.lower() in skip:
            continue
        if re.search(r"\d{2,3}-\d{2}-\d{5}", compact):
            continue
        if "대표" in compact or "전화" in compact or re.search(r"\d{2,4}-\d{3,4}-\d{4}", compact):
            continue
        if re.search(r"[가-힣]", compact):
            preferred.append(compact)
        else:
            fallback.append(compact)
    return preferred[0] if preferred else (fallback[0] if fallback else None)


def infer_receipt_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    excluded = re.compile(r"순번|상품|단가|수량|금액|과세|부가|합계|결제|카드|가맹점|승인|포인트|캐셔|전화|대표|정상|멤")
    for idx, line in enumerate(lines):
        digits = re.sub(r"\D", "", line)
        if re.fullmatch(r"\d{5,8}", digits) and idx > 0:
            candidate = re.sub(r"\s+", "", lines[idx - 1])
            if (
                candidate
                and candidate not in items
                and len(candidate) >= 2
                and re.search(r"[가-힣]", candidate)
                and not re.search(r"[\d:\[\]]", candidate)
                and not excluded.search(candidate)
                and not re.search(r"\d{2,4}-\d{3,4}-\d{4}", candidate)
            ):
                items.append(candidate)
    return items


def infer_bank_name(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if "IBK" in text or "기업은행" in compact or "중소기업은행" in compact:
        return "IBK기업은행"
    if "하나은행" in compact:
        return "하나은행"
    if "국민은행" in compact or "KB" in text:
        return "국민은행"
    if "신한은행" in compact:
        return "신한은행"
    return None


def infer_after_label(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for idx, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if any(label.lower() in compact.lower() for label in labels):
            for candidate in lines[idx : idx + 4]:
                cleaned = re.sub(r"^(예금주|예금주명|Name|Account Holder)[:\s()]*", "", candidate, flags=re.IGNORECASE).strip()
                if cleaned and cleaned != candidate:
                    return cleaned
            if idx + 1 < len(lines):
                return lines[idx + 1].strip()
    return None


def infer_vendor(text: str) -> str | None:
    patterns = [
        r"공급자\s*상호\s*[:：]?\s*([^\n]+)",
        r"상호\s*[:：]?\s*([^\n]+)",
        r"업체명\s*[:：]?\s*([^\n]+)",
        r"회사명\s*[:：]?\s*([^\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return cleanup_name(match.group(1))
    return None


def cleanup_name(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip(" :：,")
    return cleaned or None


def infer_tax_approval(text: str) -> str | None:
    match = re.search(r"승인번호\s*[:：]?\s*([0-9A-Za-z-]{6,})", text)
    return match.group(1) if match else None


def infer_item_count(text: str) -> int | None:
    count = 0
    for line in text.splitlines():
        if re.match(r"\s*\d+\s+", line) and MONEY_RE.search(line):
            count += 1
    return count or None
