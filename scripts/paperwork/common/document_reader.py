from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from scripts.ocr import extractors as ocr_extractors


BASE_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = BASE_DIR / "schemas"
DEFAULT_OCR_API_URL = "https://dhlab.gachon.ac.kr/services/rag/ocr"
DEFAULT_LITELLM_BASE_URL = "https://dhlab.gachon.ac.kr/services/litellm/v1"


@dataclass
class ReadAttempt:
    method: str
    ok: bool
    error: str = ""


@dataclass
class ReadResult:
    data: dict[str, object]
    method: str
    attempts: list[ReadAttempt] = field(default_factory=list)
    raw_text: str = ""


def load_schema(name: str) -> dict[str, object]:
    path = SCHEMA_DIR / f"{name}.yml"
    if not path.exists():
        raise FileNotFoundError(path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"schema must be a mapping: {path}")
    return loaded


def extract_json_object(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError(f"model did not return JSON: {raw!r}") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError(f"model returned non-object JSON: {parsed!r}")
    return parsed


def pdf_text(path: Path) -> str:
    return ocr_extractors.extract_text(path)


def open_json_with_retries(make_request: Callable[[], urllib.request.Request], timeout: int, label: str) -> dict[str, object]:
    retry_codes = {429, 500, 502, 503, 504}
    last_error = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(make_request(), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"{label} returned non-object JSON: {data!r}")
            return data
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[-1200:]
            last_error = f"HTTP {exc.code}: {body}"
            if exc.code in retry_codes and attempt < 3:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(f"{label} failed: {last_error}") from exc
        except urllib.error.URLError as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(f"{label} failed: {last_error}") from exc
    raise RuntimeError(f"{label} failed: {last_error}")


def text_from_ocr_response(data: dict[str, object]) -> str:
    return ocr_extractors.text_from_ocr_response(data)


def ocr_text(path: Path, api_url: str, api_key: str, timeout: int = 180) -> str:
    return ocr_extractors.ocr_api_text(path, api_url, api_key, timeout)


def litellm_json(text: str, prompt: str, base_url: str, api_key: str, model: str, timeout: int = 180, max_tokens: int = 1800) -> dict[str, object]:
    if not api_key:
        raise ValueError("LiteLLM API key is required")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text[:18000]}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    data = open_json_with_retries(
        lambda: urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        ),
        timeout,
        "LiteLLM",
    )
    message = data["choices"][0]["message"]
    return extract_json_object(message.get("content") or message.get("reasoning_content") or "")


def codex_json(path: Path, prompt: str, codex_bin: str = "codex", model: str | None = None, timeout: int = 180) -> dict[str, object]:
    return ocr_extractors.codex_json(path, prompt, codex_bin=codex_bin, model=model, timeout=timeout)


def codex_image_json(path: Path, prompt: str, codex_bin: str = "codex", model: str | None = None, timeout: int = 180) -> dict[str, object]:
    return ocr_extractors.codex_image_json(path, prompt, codex_bin=codex_bin, model=model, timeout=timeout)
