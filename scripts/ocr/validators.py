from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "generic": ("text_present",),
    "receipt": ("store_name", "generated", "total_price"),
    "bankbook_copy": ("bank_name", "account_holder", "account_number"),
    "tax_invoice": ("vendor", "issue_date", "amount"),
    "business_registration": ("vendor", "business_registration_number"),
    "estimate": ("amount",),
    "statement": ("amount",),
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    missing_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    reason: str = ""


def required_fields_for(doc_type: str, required_fields: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if required_fields is not None:
        return required_fields
    return DEFAULT_REQUIRED_FIELDS.get(doc_type, DEFAULT_REQUIRED_FIELDS["generic"])


def validate_fields(
    doc_type: str,
    data: dict[str, object],
    required_fields: tuple[str, ...] | None = None,
) -> ValidationResult:
    required = required_fields_for(doc_type, required_fields)
    missing = tuple(field for field in required if is_empty(data.get(field)))
    invalid = tuple(field for field in required if not is_empty(data.get(field)) and not field_is_valid(field, data.get(field)))
    if missing or invalid:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if invalid:
            parts.append("invalid " + ", ".join(invalid))
        return ValidationResult(False, missing, invalid, "; ".join(parts))
    return ValidationResult(True)


def is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def field_is_valid(field: str, value: object) -> bool:
    if field in {"amount", "total_price", "item_count"}:
        try:
            return int(value) > 0
        except Exception:
            return False
    if field in {"generated", "issue_date"}:
        return bool(re.match(r"^20\d{2}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?$", str(value)))
    if field == "account_number":
        return bool(re.match(r"^\d{2,6}(?:-\d{2,6}){2,4}$", str(value)))
    if field == "business_registration_number":
        return bool(re.match(r"^\d{3}-\d{2}-\d{5}$", str(value)))
    return True
