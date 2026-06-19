from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass
from difflib import SequenceMatcher

from .validators import ValidationResult, validate_fields


@dataclass(frozen=True)
class ValidationProfile:
    doc_type: str
    required_fields: tuple[str, ...]
    strict_fields: tuple[str, ...] = ()


PROFILES: dict[str, ValidationProfile] = {
    "receipt": ValidationProfile("receipt", ("store_name", "generated", "total_price")),
    "estimate": ValidationProfile("estimate", ("amount",)),
    "statement": ValidationProfile("statement", ("amount",)),
    "tax_invoice": ValidationProfile("tax_invoice", ("issue_date", "amount")),
    "bankbook_copy": ValidationProfile("bankbook_copy", ("bank_name", "account_holder", "account_number")),
    "business_registration": ValidationProfile("business_registration", ("business_registration_number",)),
}


def validate_document(
    doc_type: str,
    data: dict[str, object],
    *,
    expected_vendor: str | None = None,
) -> ValidationResult:
    profile = PROFILES.get(doc_type)
    if not profile:
        return validate_fields(doc_type, data)

    base = validate_fields(doc_type, data, profile.required_fields)
    invalid = list(base.invalid_fields)
    missing = list(base.missing_fields)
    reasons: list[str] = []
    if base.reason:
        reasons.append(base.reason)

    if doc_type == "receipt":
        validate_receipt(data, invalid, reasons)
    elif doc_type in {"estimate", "statement"}:
        validate_purchase_doc(data, invalid, reasons)
    elif doc_type == "tax_invoice":
        validate_tax_invoice(data, invalid, reasons)
    elif doc_type == "bankbook_copy":
        validate_bankbook(data, invalid, reasons, expected_vendor)
    elif doc_type == "business_registration":
        validate_business_registration(data, invalid, reasons, expected_vendor)

    ok = not missing and not invalid
    return ValidationResult(ok, tuple(sorted(set(missing))), tuple(sorted(set(invalid))), "; ".join(reasons))


def validate_receipt(data: dict[str, object], invalid: list[str], reasons: list[str]) -> None:
    store_name = str(data.get("store_name") or "")
    if store_name and re.search(r"주문번호|영수증|승인|진동벨|접수일지|매장명", store_name):
        invalid.append("store_name")
        reasons.append("receipt store_name looks like a label, not a merchant")
    total = as_int(data.get("total_price"))
    if total is not None and not 100 <= total <= 10_000_000:
        invalid.append("total_price")
        reasons.append("receipt total_price outside expected range")
    generated = str(data.get("generated") or "")
    if generated and not is_valid_date_or_datetime(generated):
        invalid.append("generated")
        reasons.append("receipt generated is not a valid normalized date/datetime")


def validate_purchase_doc(data: dict[str, object], invalid: list[str], reasons: list[str]) -> None:
    amount = as_int(data.get("amount"))
    if amount is not None and not 1_000 <= amount <= 100_000_000:
        invalid.append("amount")
        reasons.append("purchase amount outside expected range")
    item_count = data.get("item_count")
    if item_count is not None:
        parsed = as_int(item_count)
        if parsed is None or parsed < 1:
            invalid.append("item_count")
            reasons.append("item_count is not positive")


def validate_tax_invoice(data: dict[str, object], invalid: list[str], reasons: list[str]) -> None:
    amount = as_int(data.get("amount"))
    if amount is not None and not 1_000 <= amount <= 100_000_000:
        invalid.append("amount")
        reasons.append("tax invoice amount outside expected range")
    brn = data.get("business_registration_number")
    if brn and not re.match(r"^\d{3}-\d{2}-\d{5}$", str(brn)):
        invalid.append("business_registration_number")
        reasons.append("business registration number has invalid format")


def validate_bankbook(
    data: dict[str, object],
    invalid: list[str],
    reasons: list[str],
    expected_vendor: str | None,
) -> None:
    account = str(data.get("account_number") or "")
    if account and not re.match(r"^\d{2,6}(?:-\d{2,6}){2,4}$", account):
        invalid.append("account_number")
        reasons.append("account number has invalid format")
    holder = str(data.get("account_holder") or "")
    if expected_vendor and holder and vendor_similarity(expected_vendor, holder) < 0.45:
        invalid.append("account_holder")
        reasons.append(f"account holder does not resemble expected vendor: {expected_vendor}")


def validate_business_registration(
    data: dict[str, object],
    invalid: list[str],
    reasons: list[str],
    expected_vendor: str | None,
) -> None:
    vendor = str(data.get("vendor") or "")
    if expected_vendor and vendor and vendor_similarity(expected_vendor, vendor) < 0.45:
        invalid.append("vendor")
        reasons.append(f"vendor does not resemble expected vendor: {expected_vendor}")


def vendor_similarity(expected: str, actual: str) -> float:
    left = normalize_name(expected)
    right = normalize_name(actual)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_name(value: str) -> str:
    text = re.sub(r"\(주\)|주식회사|㈜|[^0-9A-Za-z가-힣]", "", value)
    return text.lower()


def as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def is_valid_date_or_datetime(value: str) -> bool:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            pass
    return False
