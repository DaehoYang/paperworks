from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import yaml


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
    return subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True)


def encode_multipart_form(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----doc-reader-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, path in files.items():
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode("utf-8"))
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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
    text = str(data.get("text") or "").strip()
    if text:
        return text
    lines: list[tuple[int, int, str]] = []
    for page in data.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for item in page.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_text = str(item.get("text") or "").strip()
            if not item_text:
                continue
            box = item.get("box") or []
            try:
                y = int(sum(point[1] for point in box) / len(box))
                x = int(sum(point[0] for point in box) / len(box))
            except Exception:
                y = int(page.get("page_index") or 0)
                x = 0
            lines.append((y, x, item_text))
    lines.sort(key=lambda row: (row[0], row[1]))
    return "\n".join(text for _y, _x, text in lines)


def ocr_text(path: Path, api_url: str, api_key: str, timeout: int = 180) -> str:
    if not api_key:
        raise ValueError("OCR API key is required")
    body, content_type = encode_multipart_form({"return_format": "json"}, {"file": path})
    data = open_json_with_retries(
        lambda: urllib.request.Request(
            api_url,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type},
            method="POST",
        ),
        timeout,
        "OCR API",
    )
    return text_from_ocr_response(data)


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
    text = ""
    try:
        text = pdf_text(path).strip()
    except Exception:
        text = ""
    if text:
        full_prompt = f"{prompt}\nDocument path: {path}\nDocument text:\n{text[:20000]}"
    else:
        full_prompt = f"{prompt}\nRead this document and return JSON only: {path}"
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)
    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append(full_prompt)
    try:
        completed = subprocess.run(command, input="", text=True, capture_output=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Codex failed with exit code {completed.returncode}\nSTDERR:\n{completed.stderr[-4000:]}")
        return extract_json_object(output_path.read_text(encoding="utf-8").strip())
    finally:
        output_path.unlink(missing_ok=True)


def codex_image_json(path: Path, prompt: str, codex_bin: str = "codex", model: str | None = None, timeout: int = 180) -> dict[str, object]:
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)
    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
        "--color",
        "never",
        "--image",
        str(path),
        "-o",
        str(output_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    try:
        completed = subprocess.run(command, input="", text=True, capture_output=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Codex image parse failed with exit code {completed.returncode}\nSTDERR:\n{completed.stderr[-4000:]}")
        return extract_json_object(output_path.read_text(encoding="utf-8").strip())
    finally:
        output_path.unlink(missing_ok=True)
