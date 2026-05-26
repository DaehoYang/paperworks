from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]


def nested_get(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def validate_required(data: dict[str, Any], required: list[str]) -> list[str]:
    errors: list[str] = []
    for field in required:
        value = nested_get(data, field)
        if value is None or value == "" or value == []:
            errors.append(f"missing required field: {field}")
    return errors


def validate_purchase(data: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
    errors = validate_required(data, list(schema.get("required_fields") or []))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty list")
        return ValidationResult(False, errors)

    item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    required = list(item_schema.get("required_fields") or [])
    numeric = list(item_schema.get("numeric_fields") or [])
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            errors.append(f"item {idx} is not an object")
            continue
        for field in required:
            if item.get(field) in (None, "", []):
                errors.append(f"item {idx} missing field: {field}")
        for field in numeric:
            value = safe_int(item.get(field))
            if value is None:
                errors.append(f"item {idx} has non-numeric field: {field}")
            elif field == "quantity" and value <= 0:
                errors.append(f"item {idx} quantity must be positive")

    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    total_price = safe_int(totals.get("total_price")) if isinstance(totals, dict) else None
    supply_price = safe_int(totals.get("supply_price")) if isinstance(totals, dict) else None
    if total_price is None and supply_price is None:
        errors.append("quote totals must include total_price or supply_price")
    return ValidationResult(not errors, errors)


def validate_meeting_or_trip(data: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
    errors = validate_required(data, list(schema.get("required_fields") or []))
    generated = data.get("generated")
    if generated:
        try:
            datetime.fromisoformat(str(generated).replace("/", "-"))
        except ValueError:
            errors.append("generated is not a valid ISO-like datetime")
    total = safe_int(data.get("total_price"))
    if total is not None and total <= 0:
        errors.append("total_price must be positive")
    task = str(schema.get("task") or "")
    receipt_type = str(data.get("receipt_type") or "").strip()
    if task == "meeting_receipt":
        receipt_types = schema.get("receipt_types") if isinstance(schema.get("receipt_types"), dict) else {}
        known = set(receipt_types.get("known") or [])
        meeting_types = set(receipt_types.get("meeting") or [])
        if known and receipt_type and receipt_type not in known:
            errors.append(f"unknown receipt_type: {receipt_type}")
        if receipt_type in meeting_types and not (str(data.get("store_name") or "").strip() or str(data.get("address") or "").strip()):
            errors.append("food/drink receipt must include store_name or address")
    if task == "trip_receipt":
        origin = str(data.get("origin") or "")
        destination = str(data.get("destination") or "")
        if not origin or not destination:
            errors.append("transport receipt must include origin and destination")
        seoul_tokens = ("서울", "서울역", "수서", "김포공항", "강남", "강서", "양재", "잠실")
        if origin and destination and not (any(token in origin for token in seoul_tokens) or any(token in destination for token in seoul_tokens)):
            errors.append("transport route must include a Seoul endpoint")
    return ValidationResult(not errors, errors)


def validate(data: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
    task = str(schema.get("task") or "")
    if task == "purchase_quote":
        return validate_purchase(data, schema)
    if task in {"meeting_receipt", "trip_receipt"}:
        return validate_meeting_or_trip(data, schema)
    return ValidationResult(True, [])
