from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scripts.ocr.extractors import extract_text


MONEY_RE = re.compile(r"(?:₩\s*)?([0-9]{1,3}(?:,[0-9]{3})+)")
TOTAL_LABEL_RE = re.compile(
    r"(?:합계금액|합\s*계\s*금\s*액|총\s*계|총계|VAT\s*포함)[^\n0-9₩]{0,30}(?:₩\s*)?([0-9]{1,3}(?:,[0-9]{3})+)",
    re.IGNORECASE,
)
SUMMARY_LINE_TOKENS = ("공급가액", "부가세", "부가세액", "세액", "합계금액", "총계", "총 계", "계 ")


@dataclass(frozen=True)
class FinancialFields:
    amount: int | None
    item_count: int | None
    item_prices: tuple[int, ...]


def parse_money(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    return int(digits)


def money_values(text: str) -> list[int]:
    values: list[int] = []
    for match in MONEY_RE.finditer(text):
        parsed = parse_money(match.group(1))
        if parsed is not None:
            values.append(parsed)
    return values


def extract_pdf_text(pdf_path: Path) -> str:
    return extract_text(pdf_path)


def extract_total_amount(text: str) -> int | None:
    candidates: list[int] = []
    for match in TOTAL_LABEL_RE.finditer(text):
        value = parse_money(match.group(1))
        if value:
            candidates.append(value)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if any(token.replace(" ", "") in compact for token in ("합계금액", "총계")):
            values = money_values(line)
            if values:
                candidates.append(max(values))
            elif idx + 1 < len(lines):
                next_values = money_values(lines[idx + 1])
                if next_values:
                    candidates.append(max(next_values))

    if candidates:
        return max(candidates)
    values = money_values(text)
    return max(values) if values else None


def looks_like_summary_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return any(token.replace(" ", "") in compact for token in SUMMARY_LINE_TOKENS)


def extract_item_prices(text: str) -> tuple[int, ...]:
    prices: list[int] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or looks_like_summary_line(line):
            continue
        values = money_values(line)
        if len(values) >= 3:
            supply_amount = values[-2]
        elif re.match(r"^\d+\s+", line) and len(values) >= 2:
            supply_amount = values[-1]
        else:
            continue
        if supply_amount not in prices:
            prices.append(supply_amount)
    return tuple(prices)


def extract_financial_fields(text: str) -> FinancialFields:
    item_prices = extract_item_prices(text)
    return FinancialFields(
        amount=extract_total_amount(text),
        item_count=len(item_prices) if item_prices else None,
        item_prices=item_prices,
    )


def extract_financial_fields_from_pdf(pdf_path: Path) -> FinancialFields:
    return extract_financial_fields(extract_pdf_text(pdf_path))


def ordered_price_similarity(left: list[int] | tuple[int, ...], right: list[int] | tuple[int, ...]) -> float:
    if not left or not right:
        return 0.0
    max_len = max(len(left), len(right))
    matches = sum(1 for a, b in zip(left, right) if a == b)
    return matches / max_len
