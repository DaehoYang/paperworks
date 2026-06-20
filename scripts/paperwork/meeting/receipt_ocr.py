from __future__ import annotations

import json
import os
import re
from pathlib import Path

from scripts.ocr import OcrConfig, read_document
from scripts.ocr.validators import ValidationResult as OcrValidationResult

from ..common import document_reader as doc_reader
from ..common import validators as common_validators
from .models import ReceiptRecord
from .paths import OCR_TEXT_DIR
from .records import parse_datetime, resolve_receipt_path, safe_int


def run_ocr_api_text(receipt_path: Path, api_url: str, api_key: str, timeout: int) -> str:
    if not api_key:
        raise ValueError("--ocr-api-key, DHLAB_OCR_API_KEY, or DHLAB_LITELLM_API_KEY is required for ocr-api-litellm")
    return doc_reader.ocr_text(receipt_path, api_url, api_key, timeout)


def run_litellm_parse(ocr_text: str, base_url: str, api_key: str, model: str, timeout: int) -> dict[str, object]:
    if not api_key:
        raise ValueError("--litellm-api-key or DHLAB_LITELLM_API_KEY is required for ocr-api-litellm")
    prompt = (
        "Extract Korean receipt fields from OCR text. Output only compact JSON with exactly these keys: "
        "total_price, generated, store_name, address, receipt_type, item_count, food_count, drink_count, "
        "transport_type, origin, destination. receipt_type must be one of food_drink, transport, lodging, "
        "office_supply, medical, fuel, other, unknown. For transport receipts, fill route origin and destination. "
        "For food/cafe receipts, fill food/drink item counts if possible."
    )
    return doc_reader.litellm_json(ocr_text, prompt, base_url, api_key, model, timeout, max_tokens=600)


def schema_for_parsed_receipt(parsed: dict[str, object]) -> dict[str, object]:
    receipt_type = infer_receipt_type(parsed)
    if receipt_type == "transport":
        return doc_reader.load_schema("trip")
    return doc_reader.load_schema("meeting")


def validate_parsed_receipt(parsed: dict[str, object]) -> None:
    schema = schema_for_parsed_receipt(parsed)
    validation = common_validators.validate(parsed, schema)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))


def validate_receipt_for_ocr(doc_type: str, parsed: dict[str, object]) -> OcrValidationResult:
    try:
        validate_parsed_receipt(parsed)
        return OcrValidationResult(True)
    except Exception as exc:
        return OcrValidationResult(False, reason=str(exc))


def parse_with_central_ocr(
    receipt_path: Path,
    *,
    methods: tuple[str, ...],
    codex_bin: str,
    model: str | None,
    timeout: int,
) -> tuple[dict[str, object], str]:
    result = read_document(
        receipt_path,
        doc_type="receipt",
        validator=validate_receipt_for_ocr,
        config=OcrConfig(methods=methods, codex_bin=codex_bin, codex_model=model, timeout=timeout),
    )
    if result.status != "validated":
        errors = []
        for attempt in result.attempts:
            detail = attempt.error or attempt.reason or "not validated"
            errors.append(f"{attempt.method}: {detail}")
        raise RuntimeError("Could not parse receipt automatically:\n" + "\n".join(errors))
    parsed = dict(result.data)
    if result.raw_text:
        parsed = fill_ocr_fallbacks(parsed, result.raw_text)
    return parsed, f"ocr:{result.method}"


def datetime_from_ocr_text(ocr_text: str) -> str:
    patterns = [
        r"(\d{4})[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})[일\s']+(\d{1,2}):(\d{2})(?::(\d{2}))?",
        r"(\d{4})(\d{2})(\d{2})['\s]*(\d{1,2}):(\d{2})(?::(\d{2}))?",
        r"(\d{4})-(\d{2})-(\d{2})(\d{2}):(\d{2})(?::(\d{2}))?",
    ]
    for pattern in patterns:
        match = re.search(pattern, ocr_text)
        if not match:
            continue
        year, month, day, hour, minute, second = match.groups(default="00")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}:{int(second):02d}"
    return ""


def fill_ocr_fallbacks(parsed: dict[str, object], ocr_text: str) -> dict[str, object]:
    updated = dict(parsed)
    generated = str(updated.get("generated") or "")
    if not re.search(r"\d{1,2}:\d{2}", generated):
        fallback = datetime_from_ocr_text(ocr_text)
        if fallback:
            updated["generated"] = fallback
    return updated


def normalize_receipt_type(value: object) -> str:
    raw = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"food": "food_drink", "beverage": "food_drink", "coffee": "food_drink", "bakery": "food_drink", "taxi": "transport", "bus": "transport", "train": "transport"}
    return aliases.get(raw, raw)


def infer_receipt_type(parsed: dict[str, object]) -> str:
    receipt_type = normalize_receipt_type(parsed.get("receipt_type"))
    if receipt_type != "unknown":
        return receipt_type
    text = f"{parsed.get('store_name', '')} {parsed.get('address', '')}".lower()
    if any(token in text for token in ("택시", "카카오t", "ktx", "srt", "코레일", "철도", "버스", "주차")):
        return "transport"
    if any(token in text for token in ("카페", "커피", "식당", "비빔밥", "국밥", "스타벅스", "투썸", "이디야", "베이커리")):
        return "food_drink"
    if safe_int(parsed.get("food_count")) or safe_int(parsed.get("drink_count")):
        return "food_drink"
    return "unknown"


def save_ocr_text(receipt_path: Path, text: str) -> str:
    if not text:
        return ""
    OCR_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = OCR_TEXT_DIR / f"{receipt_path.stem}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path.relative_to(OCR_TEXT_DIR.parents[1]))


def record_from_parsed(receipt_path: Path, parsed: dict[str, object], ocr_engine: str, ocr_text_path: str = "") -> ReceiptRecord:
    generated = parse_datetime(str(parsed.get("generated") or ""))
    total_price = safe_int(parsed.get("total_price")) or 0
    receipt_type = infer_receipt_type(parsed)
    return ReceiptRecord(
        file_name=receipt_path.name,
        receipt_path=receipt_path,
        generated=generated,
        total_price=total_price,
        store_name=str(parsed.get("store_name") or ""),
        address=str(parsed.get("address") or ""),
        receipt_type=receipt_type,
        transport_type=str(parsed.get("transport_type") or ""),
        origin=str(parsed.get("origin") or ""),
        destination=str(parsed.get("destination") or ""),
        item_count=safe_int(parsed.get("item_count")),
        food_count=safe_int(parsed.get("food_count")),
        drink_count=safe_int(parsed.get("drink_count")),
        ocr_engine=ocr_engine,
        ocr_text_path=ocr_text_path,
        ocr_result_json=json.dumps(parsed, ensure_ascii=False, sort_keys=True),
    )


def parse_receipt(
    receipt_raw: str,
    *,
    ocr_engine: str = "codex",
    codex_bin: str = "codex",
    ocr_model: str | None = None,
    ocr_timeout: int = 180,
    ocr_api_url: str = "https://dhlab.gachon.ac.kr/services/rag/ocr",
    ocr_api_key: str | None = None,
    litellm_base_url: str = "https://dhlab.gachon.ac.kr/services/litellm/v1",
    litellm_api_key: str | None = None,
    litellm_model: str = "local",
) -> ReceiptRecord:
    receipt_path = resolve_receipt_path(receipt_raw)
    if ocr_engine == "codex":
        parsed, engine = parse_with_central_ocr(
            receipt_path,
            methods=("codex_image",),
            codex_bin=codex_bin,
            model=ocr_model,
            timeout=ocr_timeout,
        )
        return record_from_parsed(receipt_path, parsed, engine)
    if ocr_engine == "ocr-api-litellm":
        api_key = ocr_api_key or litellm_api_key or os.environ.get("DHLAB_OCR_API_KEY", "") or os.environ.get("DHLAB_LITELLM_API_KEY", "")
        text = run_ocr_api_text(receipt_path, ocr_api_url, api_key, ocr_timeout)
        text_path = save_ocr_text(receipt_path, text)
        parse_key = litellm_api_key or ocr_api_key or os.environ.get("DHLAB_LITELLM_API_KEY", "") or os.environ.get("DHLAB_OCR_API_KEY", "")
        parsed = run_litellm_parse(text, litellm_base_url, parse_key, litellm_model, ocr_timeout)
        parsed = fill_ocr_fallbacks(parsed, text)
        validate_parsed_receipt(parsed)
        return record_from_parsed(receipt_path, parsed, "ocr-api-litellm", text_path)
    if ocr_engine == "auto":
        parsed, engine = parse_with_central_ocr(
            receipt_path,
            methods=("text", "codex_image"),
            codex_bin=codex_bin,
            model=ocr_model,
            timeout=ocr_timeout,
        )
        return record_from_parsed(receipt_path, parsed, engine)
    raise ValueError(f"unsupported OCR engine: {ocr_engine}")


def record_from_json(data: dict[str, object]) -> ReceiptRecord:
    receipt_path = resolve_receipt_path(str(data["file_name"]))
    parsed = dict(data)
    parsed.setdefault("generated", data.get("generated"))
    return record_from_parsed(receipt_path, parsed, str(data.get("ocr_engine") or "manual"))
