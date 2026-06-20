from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .extractors import DEFAULT_OCR_API_URL, codex_image_json, extract_text, ocr_api_text
from .parsers import parse_text
from .validators import ValidationResult, validate_fields


DEFAULT_METHODS = ("text", "codex_image")


@dataclass(frozen=True)
class OcrConfig:
    ocr_api_url: str = DEFAULT_OCR_API_URL
    ocr_api_key: str = ""
    codex_bin: str = "codex"
    codex_model: str | None = None
    timeout: int = 180
    methods: tuple[str, ...] = DEFAULT_METHODS
    include_raw_text: bool = True


@dataclass
class ReadAttempt:
    method: str
    ok: bool
    validated: bool
    elapsed_sec: float
    reason: str = ""
    error: str = ""
    data: dict[str, object] = field(default_factory=dict)


@dataclass
class ReadResult:
    source_path: str
    doc_type: str
    status: str
    method: str | None
    data: dict[str, object]
    validation: ValidationResult
    attempts: list[ReadAttempt]
    raw_text: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["validation"] = asdict(self.validation)
        data["attempts"] = [asdict(attempt) for attempt in self.attempts]
        return data


def read_document(
    path: str | Path,
    *,
    doc_type: str = "generic",
    required_fields: tuple[str, ...] | None = None,
    validator: Callable[[str, dict[str, object]], ValidationResult] | None = None,
    config: OcrConfig | None = None,
) -> ReadResult:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    cfg = config or OcrConfig(ocr_api_key=env_ocr_api_key())
    attempts: list[ReadAttempt] = []
    last_data: dict[str, object] = {}
    last_validation = validate_result(doc_type, last_data, required_fields, validator)
    last_raw_text = ""

    for method in cfg.methods:
        start = time.time()
        try:
            data, raw_text = run_method(source, method, doc_type, cfg)
            validation = validate_result(doc_type, data, required_fields, validator)
            elapsed = round(time.time() - start, 2)
            attempts.append(
                ReadAttempt(
                    method=method,
                    ok=True,
                    validated=validation.ok,
                    elapsed_sec=elapsed,
                    reason=validation.reason,
                    data=data,
                )
            )
            last_data = data
            last_validation = validation
            last_raw_text = raw_text
            if validation.ok:
                return ReadResult(
                    source_path=str(source),
                    doc_type=doc_type,
                    status="validated",
                    method=method,
                    data=data,
                    validation=validation,
                    attempts=attempts,
                    raw_text=raw_text if cfg.include_raw_text else "",
                )
        except Exception as exc:
            elapsed = round(time.time() - start, 2)
            attempts.append(ReadAttempt(method=method, ok=False, validated=False, elapsed_sec=elapsed, error=str(exc)))

    return ReadResult(
        source_path=str(source),
        doc_type=doc_type,
        status="review_required",
        method=attempts[-1].method if attempts else None,
        data=last_data,
        validation=last_validation,
        attempts=attempts,
        raw_text=last_raw_text if cfg.include_raw_text else "",
    )


def run_method(source: Path, method: str, doc_type: str, cfg: OcrConfig) -> tuple[dict[str, object], str]:
    if method == "text":
        raw_text = extract_text(source)
        if not raw_text.strip():
            raise ValueError("no embedded text extracted")
        return parse_text(doc_type, raw_text), raw_text
    if method == "ocr_api":
        raw_text = ocr_api_text(source, cfg.ocr_api_url, cfg.ocr_api_key, cfg.timeout)
        if not raw_text.strip():
            raise ValueError("OCR API returned empty text")
        return parse_text(doc_type, raw_text), raw_text
    if method == "codex_image":
        data = codex_image_json(source, codex_prompt(doc_type), codex_bin=cfg.codex_bin, model=cfg.codex_model, timeout=cfg.timeout)
        return data, ""
    raise ValueError(f"unknown read method: {method}")


def validate_result(
    doc_type: str,
    data: dict[str, object],
    required_fields: tuple[str, ...] | None,
    validator: Callable[[str, dict[str, object]], ValidationResult] | None,
) -> ValidationResult:
    if validator is not None:
        return validator(doc_type, data)
    return validate_fields(doc_type, data, required_fields)


def codex_prompt(doc_type: str) -> str:
    prompts = {
        "receipt": (
            "Read this Korean receipt image. Return only compact JSON with exactly these keys: "
            "store_name string or null, address string or null, generated string in YYYY-MM-DD HH:MM:SS or YYYY-MM-DD or null, "
            "total_price integer or null, receipt_type string, item_count integer or null, food_count integer or null, "
            "drink_count integer or null, transport_type string or null, origin string or null, destination string or null, "
            "approval_number string or null, card_number string or null, item_names array of strings. "
            "receipt_type must be one of food_drink, transport, lodging, office_supply, medical, fuel, other, unknown. "
            "For transport receipts, fill origin and destination. Do not include markdown."
        ),
        "bankbook_copy": (
            "Read this Korean bankbook/account-copy image. Return only compact JSON with exactly these keys: "
            "bank_name string or null, account_holder string or null, account_number string or null, "
            "issue_date string in YYYY-MM-DD or null. Do not include markdown."
        ),
        "tax_invoice": (
            "Read this Korean electronic tax invoice image. Return only compact JSON with exactly these keys: "
            "vendor string or null, issue_date string in YYYY-MM-DD or null, amount integer or null, "
            "item_count integer or null, item_prices array of integers, document_number string or null, "
            "item_code string or null, business_registration_number string or null, approval_number string or null. "
            "Do not include markdown."
        ),
        "business_registration": (
            "Read this Korean business registration certificate image. Return only compact JSON with exactly these keys: "
            "vendor string or null, business_registration_number string or null, issue_date string in YYYY-MM-DD or null. "
            "Do not include markdown."
        ),
        "estimate": (
            "Read this Korean purchase estimate/quote image. Return only compact JSON with exactly these keys: "
            "vendor string or null, issue_date string in YYYY-MM-DD or null, amount integer or null, "
            "item_count integer or null, item_prices array of integers, document_number string or null, "
            "item_code string or null, items array. Do not include markdown."
        ),
        "statement": (
            "Read this Korean transaction statement image. Return only compact JSON with exactly these keys: "
            "vendor string or null, issue_date string in YYYY-MM-DD or null, amount integer or null, "
            "item_count integer or null, item_prices array of integers, document_number string or null, "
            "item_code string or null, items array. Do not include markdown."
        ),
    }
    return prompts.get(
        doc_type,
        "Read this document image. Return only compact JSON with useful extracted fields. Do not include markdown.",
    )


def env_ocr_api_key() -> str:
    return os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY") or ""


def sidecar_path_for(source: Path) -> Path:
    return source.with_suffix(".json")


def write_result(result: ReadResult, output_path: Path, *, overwrite: bool = False) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output_path}")
    output_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
