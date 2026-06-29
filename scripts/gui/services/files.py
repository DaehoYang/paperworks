from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .paths import MEETING_DIR, PURCHASE_DIR, TRASH_DIR, assert_within_root, repo_relative


UPLOAD_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".xls",
    ".xlsx",
    ".hwp",
    ".hwpx",
    ".csv",
    ".json",
    ".txt",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
PREVIEW_TEXT_EXTENSIONS = {".txt", ".csv", ".json", ".md"}
CASE_NAME_RE = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_. -]+$")

DOC_TYPE_TOKENS = {
    "견적서": ("견적", "견적서", "quotation", "quote"),
    "거래명세서": ("거래명세", "거명", "statement"),
    "전자세금계산서": ("전자세금계산서", "세금계산서", "전세", "tax"),
    "통장사본": ("통장", "계좌", "bank"),
    "사업자등록증": ("사업자", "등록증", "business"),
    "물품검수확인서": ("물품검수", "검수확인", "inspection"),
}


@dataclass(frozen=True)
class FileInfo:
    path: Path
    rel_path: str
    name: str
    suffix: str
    size: int
    modified: str
    is_image: bool
    is_pdf: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def validate_case_name(name: str) -> str:
    cleaned = re.sub(r"\s+", "_", name.strip())
    if not cleaned:
        raise ValueError("case name is required")
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise ValueError("case name must be a single folder name")
    if not CASE_NAME_RE.match(cleaned):
        raise ValueError("case name contains unsupported characters")
    return cleaned


def list_purchase_cases() -> list[Path]:
    if not PURCHASE_DIR.exists():
        return []
    return sorted(
        [path for path in PURCHASE_DIR.iterdir() if path.is_dir() and not path.name.startswith(".")],
        key=natural_key,
    )


def should_hide_path(path: Path) -> bool:
    hidden_names = {"__pycache__", ".git", ".trash", "jobs", "trash", "ocr_text"}
    internal_names = {"meeting.sqlite3", "records.csv", "summary.csv"}
    if path.name in internal_names:
        return True
    return any(part.startswith(".") or part in hidden_names for part in path.parts)


def list_directories(base: Path, recursive: bool = True) -> list[Path]:
    base = assert_within_root(base)
    if not base.exists():
        return []
    directories = [base]
    iterator = base.rglob("*") if recursive else base.iterdir()
    for path in iterator:
        if path.is_dir() and not should_hide_path(path.relative_to(base)):
            directories.append(path)
    return sorted(set(directories), key=natural_key)


def create_directory(parent: Path, name: str) -> Path:
    dirname = validate_case_name(name)
    target = assert_within_root(parent / dirname)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    return target


def create_purchase_case(name: str) -> Path:
    case_name = validate_case_name(name)
    case_dir = assert_within_root(PURCHASE_DIR / case_name)
    if case_dir.exists():
        raise FileExistsError(f"purchase case already exists: {case_name}")
    case_dir.mkdir(parents=True)
    (case_dir / "imgs").mkdir()
    return case_dir


def file_info(path: Path) -> FileInfo:
    stat = path.stat()
    suffix = path.suffix.lower()
    return FileInfo(
        path=path,
        rel_path=repo_relative(path),
        name=path.name,
        suffix=suffix,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        is_image=suffix in IMAGE_EXTENSIONS,
        is_pdf=suffix == ".pdf",
    )


def list_files(base: Path, recursive: bool = True) -> list[FileInfo]:
    base = assert_within_root(base)
    if not base.exists():
        return []
    iterator = base.rglob("*") if recursive else base.iterdir()
    files = [
        file_info(path)
        for path in iterator
        if path.is_file() and not should_hide_path(path.relative_to(base))
    ]
    return sorted(files, key=lambda item: natural_key(item.path))


def list_directory_entries(base: Path) -> list[dict[str, object]]:
    base = assert_within_root(base)
    if not base.exists():
        return []
    entries: list[dict[str, object]] = []
    for path in sorted(base.iterdir(), key=natural_key):
        if should_hide_path(path.relative_to(base)):
            continue
        stat = path.stat()
        if path.is_dir():
            entries.append(
                {
                    "종류": "폴더",
                    "이름": path.name,
                    "경로": repo_relative(path),
                    "크기": "",
                    "수정일": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        elif path.is_file():
            entries.append(
                {
                    "종류": "파일",
                    "이름": path.name,
                    "경로": repo_relative(path),
                    "크기": human_size(stat.st_size),
                    "수정일": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
    return entries


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not find unique filename for {path.name}")


def safe_filename(name: str) -> str:
    cleaned = Path(name).name.strip().replace("\x00", "")
    if not cleaned:
        raise ValueError("empty filename")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError(f"unsupported file extension: {suffix}")
    return cleaned


def write_uploaded_file(uploaded_file: BinaryIO, target_dir: Path) -> Path:
    target_dir = assert_within_root(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(getattr(uploaded_file, "name", "uploaded.bin"))
    target = unique_path(assert_within_root(target_dir / filename))
    data = uploaded_file.getbuffer() if hasattr(uploaded_file, "getbuffer") else uploaded_file.read()
    target.write_bytes(bytes(data))
    return target


def metadata_path(case_dir: Path) -> Path:
    return assert_within_root(case_dir / ".gui_metadata.json")


def read_case_metadata(case_dir: Path) -> dict[str, object]:
    path = metadata_path(case_dir)
    if not path.exists():
        return {"uploads": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"uploads": []}
    if not isinstance(data, dict):
        return {"uploads": []}
    data.setdefault("uploads", [])
    return data


def append_case_upload_metadata(case_dir: Path, doc_type: str, paths: list[Path]) -> None:
    data = read_case_metadata(case_dir)
    uploads = data.setdefault("uploads", [])
    if not isinstance(uploads, list):
        uploads = []
        data["uploads"] = uploads
    for path in paths:
        uploads.append({"doc_type": doc_type, "path": repo_relative(path), "uploaded_at": utc_now()})
    metadata_path(case_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_purchase_uploads(case_dir: Path, uploaded_files: list[BinaryIO], doc_type: str) -> list[Path]:
    case_dir = assert_within_root(case_dir)
    if doc_type == "물품사진":
        target_dir = case_dir / "imgs"
    else:
        target_dir = case_dir
    saved = [write_uploaded_file(file, target_dir) for file in uploaded_files]
    append_case_upload_metadata(case_dir, doc_type, saved)
    return saved


def save_meeting_receipts(uploaded_files: list[BinaryIO]) -> list[Path]:
    target_dir = MEETING_DIR / "receipt"
    return [write_uploaded_file(file, target_dir) for file in uploaded_files]


def save_uploads_to_directory(target_dir: Path, uploaded_files: list[BinaryIO]) -> list[Path]:
    return [write_uploaded_file(file, target_dir) for file in uploaded_files]


def trash_file(path: Path) -> Path:
    source = assert_within_root(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = TRASH_DIR / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(target_dir / source.name)
    shutil.move(str(source), str(target))
    return target


def rename_file(path: Path, new_name: str) -> Path:
    source = assert_within_root(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)
    filename = safe_filename(new_name)
    target = assert_within_root(source.parent / filename)
    if target.exists():
        raise FileExistsError(target)
    source.rename(target)
    return target


def required_purchase_status(case_dir: Path) -> dict[str, list[str]]:
    infos = list_files(case_dir)
    status: dict[str, list[str]] = {label: [] for label in DOC_TYPE_TOKENS}
    for info in infos:
        if info.path.name.startswith("."):
            continue
        normalized = re.sub(r"\s+", "", info.name.lower())
        for label, tokens in DOC_TYPE_TOKENS.items():
            if any(token.lower() in normalized for token in tokens):
                status[label].append(info.rel_path)
    return status


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"
