from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


DEFAULT_OCR_API_URL = "https://dhlab.gachon.ac.kr/services/rag/ocr"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix in {".html", ".htm"}:
        return extract_html_text(path)
    if suffix in {".txt", ".csv", ".md", ".json"}:
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def extract_pdf_text(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        if completed.stdout.strip():
            return completed.stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def extract_html_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(raw, "html.parser").get_text("\n")
    except Exception:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "\n", raw)
        text = re.sub(r"(?s)<[^>]+>", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text)


def encode_multipart_form(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----paperworks-ocr-{uuid4().hex}"
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


def open_json_with_retries(request_factory, timeout: int, label: str) -> dict[str, object]:
    retry_codes = {429, 500, 502, 503, 504}
    last_error = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request_factory(), timeout=timeout) as response:
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


def ocr_api_text(path: Path, api_url: str, api_key: str, timeout: int = 180) -> str:
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


def codex_image_json(
    path: Path,
    prompt: str,
    *,
    codex_bin: str = "codex",
    model: str | None = None,
    timeout: int = 180,
) -> dict[str, object]:
    image_path: Path | None = path
    with tempfile.TemporaryDirectory(prefix="paperworks-ocr-") as tmp:
        if path.suffix.lower() == ".pdf":
            image_path = render_pdf_first_page(path, Path(tmp))
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
            str(image_path),
            "-o",
            str(output_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        try:
            completed = subprocess.run(command, input="", text=True, capture_output=True, timeout=timeout, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"Codex image failed with exit code {completed.returncode}\n{completed.stderr[-4000:]}")
            return extract_json_object(output_path.read_text(encoding="utf-8").strip())
        finally:
            output_path.unlink(missing_ok=True)


def render_pdf_first_page(path: Path, tmp_dir: Path) -> Path:
    out_prefix = tmp_dir / "page"
    completed = subprocess.run(
        ["pdftoppm", "-png", "-singlefile", "-f", "1", "-l", "1", "-r", "200", str(path), str(out_prefix)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    image_path = out_prefix.with_suffix(".png")
    if completed.returncode != 0 or not image_path.exists():
        raise RuntimeError(f"pdftoppm failed for {path}: {completed.stderr[-1200:]}")
    return image_path


def extract_json_object(raw: str) -> dict[str, object]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError(f"response did not contain JSON: {raw!r}") from None
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError(f"response JSON must be an object: {data!r}")
    return data
