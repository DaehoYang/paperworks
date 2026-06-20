from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.documents.amounts import extract_financial_fields_from_pdf
from scripts.documents.vendors import canonical_vendor, normalize_vendor
from scripts.ocr.reader import OcrConfig, ReadResult, read_document
from scripts.ocr.validation_profiles import validate_document


STRUCTURED_DOC_TYPES = {
    "tax_invoice",
    "estimate",
    "statement",
    "business_registration",
    "bankbook_copy",
}


def enrich_metadata_from_pdf(
    metadata: dict[str, Any],
    pdf_path: Path,
    *,
    allow_codex: bool = True,
    force_codex: bool = False,
    timeout: int = 180,
) -> dict[str, Any]:
    enriched = dict(metadata)
    doc_type = str(enriched.get("doc_type") or "unknown")
    fields = extract_financial_fields_from_pdf(pdf_path)
    merge_structured_fields(
        enriched,
        {
            "amount": fields.amount,
            "item_count": fields.item_count,
            "item_prices": list(fields.item_prices),
        },
        overwrite=False,
    )

    if doc_type not in STRUCTURED_DOC_TYPES:
        return enriched

    if validate_document(doc_type, structured_payload(enriched)).ok and not force_codex:
        enriched.setdefault("ocr_status", "validated_cached")
        return enriched

    methods = ("text", "codex_image") if allow_codex else ("text",)
    if enriched.get("ocr_codex_attempted") and not force_codex:
        methods = ("text",)

    result = read_document(
        pdf_path,
        doc_type=doc_type,
        validator=lambda dtype, data: validate_document(dtype, data),
        config=OcrConfig(methods=methods, include_raw_text=False, timeout=timeout),
    )
    merge_read_result(enriched, result)
    return enriched


def merge_read_result(metadata: dict[str, Any], result: ReadResult) -> None:
    merge_structured_fields(metadata, result.data, overwrite=False)
    metadata["ocr_status"] = result.status
    metadata["ocr_method"] = result.method
    metadata["ocr_validation"] = {
        "ok": result.validation.ok,
        "missing_fields": list(result.validation.missing_fields),
        "invalid_fields": list(result.validation.invalid_fields),
        "reason": result.validation.reason,
    }
    metadata["ocr_attempts"] = [
        {
            "method": attempt.method,
            "ok": attempt.ok,
            "validated": attempt.validated,
            "elapsed_sec": attempt.elapsed_sec,
            "reason": attempt.reason,
            "error": attempt.error,
        }
        for attempt in result.attempts
    ]
    metadata["ocr_codex_attempted"] = any(attempt.method == "codex_image" for attempt in result.attempts)
    metadata["ocr_codex_validated"] = any(attempt.method == "codex_image" and attempt.validated for attempt in result.attempts)


def merge_structured_fields(metadata: dict[str, Any], data: dict[str, Any], *, overwrite: bool) -> None:
    for key in (
        "vendor",
        "issue_date",
        "amount",
        "item_count",
        "document_number",
        "item_code",
        "bank_name",
        "account_holder",
        "account_number",
        "business_registration_number",
    ):
        value = data.get(key)
        if value_is_empty(value):
            continue
        if overwrite or value_is_empty(metadata.get(key)):
            metadata[key] = normalize_value(key, value)

    item_prices = normalize_item_prices(data.get("item_prices"))
    if item_prices and (overwrite or not normalize_item_prices(metadata.get("item_prices"))):
        metadata["item_prices"] = item_prices
        metadata["item_count"] = metadata.get("item_count") or len(item_prices)

    vendor = metadata.get("vendor")
    if isinstance(vendor, str) and vendor:
        canonical = canonical_vendor(vendor) or vendor
        metadata["vendor"] = canonical
        if canonical != vendor:
            metadata.setdefault("original_vendor", vendor)
        metadata["normalized_vendor"] = normalize_vendor(canonical)


def structured_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "vendor": metadata.get("vendor"),
        "issue_date": metadata.get("issue_date"),
        "amount": metadata.get("amount"),
        "item_count": metadata.get("item_count"),
        "item_prices": normalize_item_prices(metadata.get("item_prices")),
        "document_number": metadata.get("document_number"),
        "item_code": metadata.get("item_code"),
        "bank_name": metadata.get("bank_name"),
        "account_holder": metadata.get("account_holder"),
        "account_number": metadata.get("account_number"),
        "business_registration_number": metadata.get("business_registration_number"),
    }


def normalize_value(key: str, value: Any) -> Any:
    if key in {"amount", "item_count"}:
        try:
            return int(value)
        except Exception:
            return value
    return value


def normalize_item_prices(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    prices: list[int] = []
    for item in value:
        try:
            parsed = int(item)
        except Exception:
            continue
        if parsed > 0:
            prices.append(parsed)
    return prices


def value_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False
