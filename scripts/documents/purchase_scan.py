from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from scripts.documents.classifiers import DOC_TYPES, document_types_from_filename, extract_codes
from scripts.documents.vendors import parse_case_name


DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".hwp", ".hwpx", ".xls", ".xlsx"}
IGNORED_NAMES = {"items.xls", "물품검수확인서_작성.pdf", "물품검수확인서.pdf"}
IGNORED_DIRS = {
    "imgs",
    "imgs1",
    "img",
    "other imgs",
    "_common",
    "_공통자료",
    "vendors",
    "venders",
    "__pycache__",
}


@dataclass
class PurchaseCase:
    path: Path
    case_date: str | None
    vendor: str | None
    normalized_vendor: str
    legacy: bool
    document_number: str | None = None
    item_code: str | None = None
    local_docs: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    def as_db_dict(self) -> dict:
        return {
            "case_dir": str(self.path),
            "case_name": self.name,
            "case_date": self.case_date,
            "vendor": self.vendor,
            "normalized_vendor": self.normalized_vendor,
            "document_number": self.document_number,
            "item_code": self.item_code,
        }


def is_document_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in DOCUMENT_EXTENSIONS and path.name not in IGNORED_NAMES


def immediate_document_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return [item for item in sorted(path.iterdir()) if is_document_file(item)]


def has_document_files(path: Path) -> bool:
    return bool(immediate_document_files(path))


def discover_purchase_cases(root: Path) -> list[Path]:
    root = root.resolve()
    if root.name in IGNORED_DIRS:
        return []
    if has_document_files(root):
        child_cases = [
            child
            for child in sorted(root.iterdir())
            if child.is_dir() and child.name not in IGNORED_DIRS and has_document_files(child)
        ]
        if child_cases and root.name.startswith(tuple(str(i) for i in range(10))):
            return child_cases
        return [root]

    result: list[Path] = []
    for child in sorted(root.iterdir()) if root.exists() and root.is_dir() else []:
        if not child.is_dir() or child.name in IGNORED_DIRS:
            continue
        result.extend(discover_purchase_cases(child))
    return result


def _ordered_doc_types(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value in DOC_TYPES and value not in result:
            result.append(value)
    return result


def sidecar_document_types(file_path: Path) -> list[str]:
    json_path = file_path.with_suffix(".json")
    if not json_path.exists():
        return []
    try:
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    values: list[str] = []
    raw_all = metadata.get("all_doc_types")
    if isinstance(raw_all, list):
        values.extend(item for item in raw_all if isinstance(item, str))

    raw_all_json = metadata.get("all_doc_types_json")
    if isinstance(raw_all_json, str) and raw_all_json:
        try:
            parsed = json.loads(raw_all_json)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            values.extend(item for item in parsed if isinstance(item, str))

    raw_doc_type = metadata.get("doc_type")
    if isinstance(raw_doc_type, str):
        values.append(raw_doc_type)
    return _ordered_doc_types(values)


def scan_purchase_case(path: Path) -> PurchaseCase:
    parsed = parse_case_name(path)
    docs: dict[str, list[Path]] = {}
    code_texts: list[str] = [path.name]
    for file_path in immediate_document_files(path):
        code_texts.append(file_path.name)
        doc_types = _ordered_doc_types([*sidecar_document_types(file_path), *document_types_from_filename(file_path.name)])
        for doc_type in doc_types:
            docs.setdefault(doc_type, []).append(file_path)
    document_number, item_code = extract_codes("\n".join(code_texts))
    return PurchaseCase(
        path=path,
        case_date=parsed.case_date,
        vendor=parsed.vendor,
        normalized_vendor=parsed.normalized_vendor,
        legacy=parsed.legacy,
        document_number=document_number,
        item_code=item_code,
        local_docs=docs,
    )


def scan_purchase_root(root: Path) -> list[PurchaseCase]:
    return [scan_purchase_case(path) for path in discover_purchase_cases(root)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
