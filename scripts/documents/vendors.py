from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


CORPORATE_TOKENS = (
    "주식회사",
    "(주)",
    "㈜",
    "유한회사",
    "합자회사",
    "합명회사",
)


def normalize_vendor(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    for token in CORPORATE_TOKENS:
        text = text.replace(token.lower(), "")
    text = re.sub(r"[\s()\[\]{}·.,/_\\-]+", "", text)
    return text


def safe_name(value: str, fallback: str = "unknown") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:80] or fallback


def parse_yymmdd(value: str) -> str | None:
    match = re.match(r"^(\d{2})(\d{2})(\d{2})$", value)
    if not match:
        return None
    yy, mm, dd = map(int, match.groups())
    try:
        parsed = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    return parsed.isoformat()


@dataclass(frozen=True)
class CaseName:
    case_date: str | None
    vendor: str | None
    normalized_vendor: str
    legacy: bool


def parse_case_name(path: Path) -> CaseName:
    name = path.name
    standard = re.match(r"^(\d{6})_(.+)$", name)
    if standard:
        case_date = parse_yymmdd(standard.group(1))
        vendor = standard.group(2).strip() or None
        if vendor and re.search(r"[가-힣]", vendor):
            return CaseName(case_date, vendor, normalize_vendor(vendor), legacy=False)
        if vendor and "_" in vendor:
            legacy_vendor = vendor.rsplit("_", 1)[-1].strip() or None
            return CaseName(case_date, legacy_vendor, normalize_vendor(legacy_vendor), legacy=True)
        return CaseName(case_date, None, "", legacy=True)

    legacy_source = path
    if re.match(r"^\d+번$", name) and path.parent != path:
        legacy_source = path.parent
    legacy = re.match(r"^(\d{6})(?:_(.*))?$", legacy_source.name)
    if not legacy:
        return CaseName(None, None, "", legacy=True)

    case_date = parse_yymmdd(legacy.group(1))
    rest = (legacy.group(2) or "").strip()
    vendor = None
    if rest and "_" in rest:
        vendor = rest.rsplit("_", 1)[-1].strip() or None
    return CaseName(case_date, vendor, normalize_vendor(vendor), legacy=True)
