from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.documents.classifiers import (
    DOC_TYPE_LABELS,
    missing_documents_for_doc_types,
    purchase_status_from_doc_types,
    required_documents_for_doc_types,
)
from scripts.documents.purchase_scan import scan_purchase_root
from scripts.gui.services import files as file_services
from scripts.gui.services import jobs as job_services
from scripts.gui.services import paperwork as paperwork_services
from scripts.gui.services import projects as project_services
from scripts.gui.services.paths import MEETING_DIR, PURCHASE_DIR, ROOT_DIR, TRASH_DIR, assert_within_root, repo_relative


FRONTEND_DIST = ROOT_DIR / "scripts" / "gui" / "frontend" / "dist"
ALLOWED_ROOTS = {
    "purchase": PURCHASE_DIR,
    "meeting": MEETING_DIR,
}
BLOCKED_NAMES = {
    ".git",
    "__pycache__",
    "secret.json",
    "credentials.json",
    "token.json",
    ".env",
}
BLOCKED_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".env", ".key", ".pem"}
ALLOWED_UPLOAD_SUFFIXES = file_services.UPLOAD_EXTENSIONS


@dataclass(frozen=True)
class ResolvedPath:
    root_key: str
    root: Path
    path: Path


class CreateFolderRequest(BaseModel):
    parentPath: str
    name: str


class RenameRequest(BaseModel):
    path: str
    newName: str


class DeleteRequest(BaseModel):
    paths: list[str]


class MoveRequest(BaseModel):
    paths: list[str]
    destinationPath: str
    operation: str = "move"


class DeletePurchaseImageRequest(BaseModel):
    casePath: str
    path: str


class PurchaseImageAssignment(BaseModel):
    path: str
    itemNumber: int


class ReorderPurchaseImagesRequest(BaseModel):
    casePath: str
    assignments: list[PurchaseImageAssignment]


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def reject_blocked(path: Path) -> None:
    parts = set(path.parts)
    if parts & BLOCKED_NAMES:
        raise HTTPException(status_code=403, detail="blocked path")
    if path.name in BLOCKED_NAMES or path.suffix.lower() in BLOCKED_SUFFIXES:
        raise HTTPException(status_code=403, detail="blocked file type")


def resolve_path(value: str) -> ResolvedPath:
    if not value or value == "/":
        raise HTTPException(status_code=400, detail="root key is required")
    normalized = "/" + value.strip("/")
    parts = [part for part in normalized.split("/") if part]
    root_key = parts[0]
    root = ALLOWED_ROOTS.get(root_key)
    if root is None:
        raise HTTPException(status_code=403, detail="unknown root")
    candidate = root.joinpath(*parts[1:]) if len(parts) > 1 else root
    try:
        resolved = assert_within_root(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise HTTPException(status_code=403, detail="path escapes allowed root")
    reject_blocked(resolved)
    return ResolvedPath(root_key=root_key, root=root_resolved, path=resolved)


def api_path(path: Path) -> str:
    rel = repo_relative(path)
    return "/" + rel


def parent_api_path(path: Path) -> str:
    return api_path(path.parent)


def file_item(path: Path) -> dict[str, object]:
    stat = path.stat()
    is_dir = path.is_dir()
    item: dict[str, object] = {
        "name": path.name,
        "isDirectory": is_dir,
        "path": api_path(path),
        "updatedAt": utc_iso(stat.st_mtime),
    }
    if not is_dir:
        item["size"] = stat.st_size
        item["mimeType"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return item


def visible_path(path: Path) -> bool:
    try:
        reject_blocked(path)
    except HTTPException:
        return False
    if any(part.startswith(".") for part in path.relative_to(ROOT_DIR).parts):
        return False
    if "jobs" in path.parts or "trash" in path.parts:
        return False
    return True


def collect_files(root: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = [file_item(root)]
    for path in root.rglob("*"):
        if not visible_path(path):
            continue
        if path.is_dir() or path.is_file():
            items.append(file_item(path))
    return sorted(items, key=lambda item: (str(item["path"]).count("/"), str(item["path"]).casefold()))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def unique_destination(path: Path) -> Path:
    return file_services.unique_path(path)


def safe_child_name(name: str, allow_file: bool = False) -> str:
    if allow_file:
        return file_services.safe_filename(name)
    return file_services.validate_case_name(name)


def action_job_response(jobs: list[job_services.Job]) -> dict[str, object]:
    return {
        "jobs": [
            {
                "id": job.id,
                "kind": job.status.get("kind"),
                "state": job.status.get("state"),
                "createdAt": job.status.get("created_at"),
            }
            for job in jobs
        ]
    }


def purchase_image_paths(case_dir: Path) -> list[Path]:
    candidates = [case_dir / "imgs", case_dir / "imgs1", case_dir / "img", case_dir]
    image_paths: list[Path] = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        image_paths.extend(
            path
            for path in sorted(directory.iterdir())
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in file_services.IMAGE_EXTENSIONS
        )
    return image_paths


def has_purchase_images(case_dir: Path) -> bool:
    return bool(purchase_image_paths(case_dir))


def needs_purchase_generation(case_dir: Path) -> bool:
    return not (case_dir / "items.xls").exists() or not (case_dir / "물품검수확인서_작성.pdf").exists()


def purchase_generated(case_dir: Path) -> bool:
    return (case_dir / "items.xls").exists() and (case_dir / "물품검수확인서_작성.pdf").exists()


def read_purchase_metadata(case_dir: Path) -> dict[str, object]:
    path = case_dir / ".paperworks.yml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def purchase_uploaded(case_dir: Path) -> bool:
    metadata = read_purchase_metadata(case_dir)
    workflow = metadata.get("workflow")
    if isinstance(workflow, dict) and workflow.get("uploaded") is True:
        return True
    status = metadata.get("status")
    return isinstance(status, dict) and status.get("uploaded") is True


def purchase_workflow_label(doc_status: str, case_dir: Path) -> dict[str, object]:
    image_count = len(purchase_image_paths(case_dir))
    generated = purchase_generated(case_dir)
    uploaded = purchase_uploaded(case_dir)
    if uploaded:
        workflow_status = "uploaded"
    elif generated:
        workflow_status = "generated"
    elif image_count:
        workflow_status = "images found"
    else:
        workflow_status = "no images"
    return {
        "workflowStatus": workflow_status,
        "statusLabel": f"{doc_status} · {workflow_status}",
        "imageCount": image_count,
        "generated": generated,
        "uploaded": uploaded,
    }


def quote_file_for_case(case_dir: Path) -> Path | None:
    try:
        from scripts.paperwork.purchase.process_purchase import find_quote_file
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load purchase quote selector. Install/repair purchase dependencies and retry. {exc}") from exc
    try:
        return find_quote_file(case_dir)
    except FileNotFoundError:
        return None


def item_count_from_items_xls(case_dir: Path) -> int | None:
    items_path = case_dir / "items.xls"
    if items_path.exists():
        try:
            import xlrd

            book = xlrd.open_workbook(str(items_path))
            sheet = book.sheet_by_index(0)
            count = 0
            for row_index in range(1, sheet.nrows):
                values = [str(sheet.cell_value(row_index, col_index)).strip() for col_index in range(sheet.ncols)]
                if any(values):
                    count += 1
            return count or None
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read generated items.xls. Install/repair xlrd and retry. {exc}") from exc
    return None


def item_count_from_purchase_parser(quote_path: Path | None) -> int | None:
    if not quote_path:
        return None
    try:
        from scripts.paperwork.purchase.process_purchase import (
            DEFAULT_LITELLM_BASE_URL,
            DEFAULT_OCR_API_URL,
            parse_quote_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load purchase parser. Install/repair purchase dependencies and retry. {exc}") from exc
    try:
        items, _mode, _totals = parse_quote_items(
            quote_path,
            parse_engine="auto",
            ocr_api_url=os.environ.get("DHLAB_OCR_API_URL", DEFAULT_OCR_API_URL),
            ocr_api_key=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", ""),
            litellm_base_url=os.environ.get("DHLAB_LITELLM_BASE_URL", DEFAULT_LITELLM_BASE_URL),
            litellm_api_key=os.environ.get("DHLAB_LITELLM_API_KEY", ""),
            litellm_model=os.environ.get("DHLAB_LITELLM_MODEL", "local"),
            codex_bin="codex",
            codex_model=None,
            timeout=180,
        )
        return len(items) or None
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse quote with purchase parser. {exc}") from exc


def item_count_from_quote(quote_path: Path | None, case_dir: Path) -> int:
    items_path = case_dir / "items.xls"
    if items_path.exists() and (not quote_path or items_path.stat().st_mtime >= quote_path.stat().st_mtime):
        count = item_count_from_items_xls(case_dir)
        if count:
            return count
    if quote_path:
        count = item_count_from_purchase_parser(quote_path)
        if count:
            return count
        raise HTTPException(status_code=422, detail="Purchase parser returned no quote items.")
    count = item_count_from_items_xls(case_dir)
    if count:
        return count
    raise HTTPException(status_code=422, detail="Cannot determine item count without a quote file or generated items.xls.")


def numbered_purchase_images(case_dir: Path) -> list[tuple[int, Path]]:
    result: list[tuple[int, Path]] = []
    for path in purchase_image_paths(case_dir):
        match = re.match(r"^(\d+)(?:[_-]|(?=\.))", path.name)
        number = int(match.group(1)) if match else 0
        result.append((number, path))
    return sorted(result, key=lambda row: (row[0] or 9999, row[1].name.casefold()))


def image_upload_name(filename: str, item_number: int) -> str:
    safe = file_services.safe_filename(filename)
    return f"{item_number:03d}_{safe}"


def image_base_name(path: Path) -> str:
    return re.sub(r"^\d+[_-]+", "", path.name) or path.name


def archived_purchase_image_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = TRASH_DIR / stamp / "purchase-images"
    target_dir.mkdir(parents=True, exist_ok=True)
    return unique_destination(target_dir / path.name)


def archive_purchase_image(path: Path) -> Path:
    target = archived_purchase_image_path(path)
    shutil.move(str(path), str(target))
    return target


def existing_images_for_item(case_dir: Path, item_number: int, exclude: set[Path] | None = None) -> list[Path]:
    excluded = {path.resolve() for path in (exclude or set())}
    return [
        path
        for number, path in numbered_purchase_images(case_dir)
        if (number == item_number or (number == 0 and item_number == 1)) and path.resolve() not in excluded
    ]


def resolve_purchase_image_path(case_dir: Path, value: str) -> Path:
    resolved = resolve_path(value)
    if resolved.root_key != "purchase" or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    case_resolved = case_dir.resolve()
    image_resolved = resolved.path.resolve()
    if image_resolved != case_resolved and case_resolved not in image_resolved.parents:
        raise HTTPException(status_code=403, detail="image must be inside purchase case")
    if image_resolved.suffix.lower() not in file_services.IMAGE_EXTENSIONS:
        raise HTTPException(status_code=403, detail="path is not a supported image")
    return image_resolved


def validate_item_number(item_number: int, item_count: int) -> None:
    if item_number < 1 or item_number > item_count:
        raise HTTPException(status_code=400, detail=f"item number must be between 1 and {item_count}")


def purchase_image_info(path: Path, item_number: int | None = None) -> dict[str, object]:
    if item_number is None:
        match = re.match(r"^(\d+)(?:[_-]|(?=\.))", path.name)
        item_number = int(match.group(1)) if match else 0
    return {
        "name": path.name,
        "path": api_path(path),
        "itemNumber": item_number or None,
        "size": path.stat().st_size,
        "updatedAt": utc_iso(path.stat().st_mtime),
    }


def resolve_purchase_case_path(value: str) -> Path:
    resolved = resolve_path(value)
    purchase_root = PURCHASE_DIR.resolve()
    if resolved.root_key != "purchase":
        raise HTTPException(status_code=403, detail="purchase case path is required")
    if resolved.path.resolve() == purchase_root:
        raise HTTPException(status_code=400, detail="purchase case path is required")
    if not resolved.path.exists() or not resolved.path.is_dir():
        raise HTTPException(status_code=404, detail="purchase case not found")
    return resolved.path


def active_job_for_kind(kind: str) -> job_services.Job | None:
    for job in job_services.list_jobs(limit=100):
        if job.status.get("kind") == kind and job.status.get("state") in {"queued", "running"}:
            return job
    return None


def reject_duplicate_action(kind: str) -> None:
    job = active_job_for_kind(kind)
    if job:
        raise HTTPException(status_code=409, detail=f"{kind} is already {job.status.get('state')} as job {job.id}.")


app = FastAPI(title="Paperworks React GUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/files")
def list_files() -> dict[str, object]:
    roots = [{"name": key, "isDirectory": True, "path": f"/{key}", "updatedAt": utc_iso(path.stat().st_mtime)} for key, path in ALLOWED_ROOTS.items()]
    children: list[dict[str, object]] = []
    for root in ALLOWED_ROOTS.values():
        if root.exists():
            children.extend(collect_files(root)[1:])
    return {"files": roots + children}


@app.get("/api/dashboard")
def dashboard() -> dict[str, object]:
    purchase_cases: list[dict[str, object]] = []
    for case in scan_purchase_root(PURCHASE_DIR):
        if not case.path.name[:1].isdigit():
            continue
        present_types = set(case.local_docs)
        status = purchase_status_from_doc_types(present_types)
        required_doc_types = required_documents_for_doc_types(present_types)
        missing = [DOC_TYPE_LABELS[doc_type] for doc_type in missing_documents_for_doc_types(present_types)]
        required = {
            DOC_TYPE_LABELS[doc_type]: [repo_relative(path) for path in case.local_docs.get(doc_type, [])]
            for doc_type in required_doc_types
        }
        workflow = purchase_workflow_label(status, case.path)
        infos = file_services.list_files(case.path, recursive=True)
        purchase_cases.append(
            {
                "name": case.path.name,
                "path": api_path(case.path),
                "status": status,
                **workflow,
                "missing": missing,
                "required": required,
                "fileCount": len(infos),
                "updatedAt": utc_iso(case.path.stat().st_mtime),
            }
        )

    receipt_dir = MEETING_DIR / "receipt"
    output_dir = MEETING_DIR / "output"
    receipts = file_services.list_files(receipt_dir, recursive=False) if receipt_dir.exists() else []
    outputs = file_services.list_files(output_dir, recursive=False) if output_dir.exists() else []

    recent_jobs = []
    for job in job_services.list_jobs(limit=12):
        recent_jobs.append(
            {
                "id": job.id,
                "kind": job.status.get("kind"),
                "state": job.status.get("state"),
                "returncode": job.status.get("returncode"),
                "createdAt": job.status.get("created_at"),
                "finishedAt": job.status.get("finished_at"),
            }
        )

    return {
        "projects": [project.__dict__ for project in project_services.load_projects()],
        "purchaseCases": purchase_cases,
        "meeting": {
            "receiptCount": len(receipts),
            "outputCount": len(outputs),
            "recordsCsv": (receipt_dir / "records.csv").exists(),
            "summaryCsv": (receipt_dir / "summary.csv").exists(),
        },
        "jobs": recent_jobs,
    }


@app.get("/api/jobs")
def list_jobs() -> dict[str, object]:
    jobs = []
    for job in job_services.list_jobs(limit=100):
        jobs.append(
            {
                "id": job.id,
                "kind": job.status.get("kind"),
                "state": job.status.get("state"),
                "returncode": job.status.get("returncode"),
                "createdAt": job.status.get("created_at"),
                "startedAt": job.status.get("started_at"),
                "finishedAt": job.status.get("finished_at"),
                "caseDir": job.status.get("case_dir"),
                "count": job.status.get("count"),
            }
        )
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": {"id": job.id, **job.status, "command": job_services.command_for_job(job)}}


@app.get("/api/jobs/{job_id}/stdout")
def get_job_stdout(job_id: str) -> dict[str, str]:
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"text": job_services.read_log(job, "stdout.log")}


@app.get("/api/jobs/{job_id}/stderr")
def get_job_stderr(job_id: str) -> dict[str, str]:
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"text": job_services.read_log(job, "stderr.log")}


@app.post("/api/actions/collect_docs")
def collect_docs() -> dict[str, object]:
    reject_duplicate_action("collect_docs")
    job = job_services.start_job(
        "collect_docs",
        [sys.executable, "-u", "scripts/documents/run_daily.py"],
        metadata={"target": "purchase"},
        cwd=ROOT_DIR,
    )
    return action_job_response([job])


@app.post("/api/actions/generate_purchase_docs")
def generate_purchase_docs() -> dict[str, object]:
    reject_duplicate_action("generate_purchase_docs")
    jobs: list[job_services.Job] = []
    skipped: list[dict[str, str]] = []
    for case in scan_purchase_root(PURCHASE_DIR):
        if not case.path.name[:1].isdigit():
            continue
        status = purchase_status_from_doc_types(set(case.local_docs))
        if status not in {"ready", "finished"}:
            skipped.append({"case": case.name, "reason": f"status={status}"})
            continue
        if not has_purchase_images(case.path):
            skipped.append({"case": case.name, "reason": "missing images"})
            continue
        if not needs_purchase_generation(case.path):
            skipped.append({"case": case.name, "reason": "already generated"})
            continue
        jobs.append(
            job_services.start_job(
                "generate_purchase_docs",
                paperwork_services.process_purchase_command(case.path),
                metadata={"case_dir": repo_relative(case.path), "case_status": status},
                cwd=ROOT_DIR,
            )
        )
    if not jobs:
        detail = "No purchase cases need document generation."
        if skipped:
            detail += " Checked cases were skipped because they are already generated, missing images, or not ready."
        raise HTTPException(status_code=409, detail=detail)
    response = action_job_response(jobs)
    response["skipped"] = skipped
    return response


@app.post("/api/actions/upload_purchases")
def upload_purchases() -> dict[str, object]:
    raise HTTPException(status_code=409, detail="Upload Purchases requires project selection and confirmation UI before portal upload.")


@app.post("/api/actions/process_receipts")
def process_receipts() -> dict[str, object]:
    reject_duplicate_action("process_receipts")
    receipt_dir = MEETING_DIR / "receipt"
    receipt_paths: list[Path] = []
    if receipt_dir.exists() and receipt_dir.is_dir():
        receipt_paths = [
            path
            for path in sorted(receipt_dir.iterdir())
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in {".pdf", *file_services.IMAGE_EXTENSIONS}
        ]
    if not receipt_paths:
        raise HTTPException(status_code=409, detail="No receipt files found in meeting/receipt.")
    job = job_services.start_job(
        "process_receipts",
        paperwork_services.process_receipts_command(receipt_paths),
        metadata={"count": len(receipt_paths), "target": "meeting/receipt"},
        cwd=ROOT_DIR,
    )
    return action_job_response([job])


@app.get("/api/purchase-image-helper")
def purchase_image_helper(casePath: str) -> dict[str, object]:
    case_dir = resolve_purchase_case_path(casePath)
    quote_path = quote_file_for_case(case_dir)
    item_count = item_count_from_quote(quote_path, case_dir)
    images = [purchase_image_info(path, item_number) for item_number, path in numbered_purchase_images(case_dir)]
    return {
        "casePath": api_path(case_dir),
        "caseName": case_dir.name,
        "quotePath": api_path(quote_path) if quote_path else None,
        "itemCount": item_count,
        "images": images,
    }


@app.post("/api/purchase-image-helper/upload")
async def upload_purchase_images(
    casePath: Annotated[str, Form()],
    itemNumbers: Annotated[str, Form()],
    file: Annotated[list[UploadFile], File()],
) -> dict[str, object]:
    case_dir = resolve_purchase_case_path(casePath)
    try:
        parsed_numbers = json.loads(itemNumbers)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="itemNumbers must be a JSON array") from exc
    if not isinstance(parsed_numbers, list) or len(parsed_numbers) != len(file):
        raise HTTPException(status_code=400, detail="itemNumbers length must match uploaded files")
    quote_path = quote_file_for_case(case_dir)
    item_count = item_count_from_quote(quote_path, case_dir)
    target_dir = case_dir / "imgs"
    target_dir.mkdir(parents=True, exist_ok=True)
    item_numbers: list[int] = []
    for raw_number in parsed_numbers:
        try:
            item_number = int(raw_number)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="item number must be an integer") from exc
        validate_item_number(item_number, item_count)
        item_numbers.append(item_number)
    if len(set(item_numbers)) != len(item_numbers):
        raise HTTPException(status_code=400, detail="only one uploaded image is allowed per item number")

    saved = []
    archived = []
    for upload_file, item_number in zip(file, item_numbers):
        filename = upload_file.filename or "uploaded.jpg"
        suffix = Path(filename).suffix.lower()
        if suffix not in file_services.IMAGE_EXTENSIONS:
            raise HTTPException(status_code=403, detail=f"unsupported image type: {suffix}")
        for existing in existing_images_for_item(case_dir, item_number):
            archived.append(api_path(archive_purchase_image(existing)))
        target = target_dir / image_upload_name(filename, item_number)
        target = unique_destination(target)
        with target.open("wb") as handle:
            while chunk := await upload_file.read(1024 * 1024):
                handle.write(chunk)
        saved.append(purchase_image_info(target, item_number))
    return {"uploaded": saved, "archived": archived, "itemCount": item_count}


@app.post("/api/purchase-image-helper/delete")
def delete_purchase_image(payload: DeletePurchaseImageRequest) -> dict[str, object]:
    case_dir = resolve_purchase_case_path(payload.casePath)
    image_path = resolve_purchase_image_path(case_dir, payload.path)
    archived_path = archive_purchase_image(image_path)
    return {"deleted": payload.path, "archived": api_path(archived_path)}


@app.post("/api/purchase-image-helper/reorder")
def reorder_purchase_images(payload: ReorderPurchaseImagesRequest) -> dict[str, object]:
    case_dir = resolve_purchase_case_path(payload.casePath)
    quote_path = quote_file_for_case(case_dir)
    item_count = item_count_from_quote(quote_path, case_dir)
    if not payload.assignments:
        return {"images": [purchase_image_info(path, item_number) for item_number, path in numbered_purchase_images(case_dir)]}

    targets: list[tuple[Path, int]] = []
    seen_paths: set[Path] = set()
    seen_numbers: set[int] = set()
    for assignment in payload.assignments:
        image_path = resolve_purchase_image_path(case_dir, assignment.path)
        validate_item_number(assignment.itemNumber, item_count)
        if image_path in seen_paths:
            raise HTTPException(status_code=400, detail="duplicate image path in assignments")
        if assignment.itemNumber in seen_numbers:
            raise HTTPException(status_code=400, detail="only one existing image is allowed per item number")
        seen_paths.add(image_path)
        seen_numbers.add(assignment.itemNumber)
        targets.append((image_path, assignment.itemNumber))

    for _source, item_number in targets:
        for existing in existing_images_for_item(case_dir, item_number, exclude={source for source, _number in targets}):
            archive_purchase_image(existing)

    temp_moves: list[tuple[Path, Path, int]] = []
    for source, item_number in targets:
        temp = unique_destination(source.parent / f".reorder-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{source.name}")
        source.rename(temp)
        temp_moves.append((temp, source, item_number))

    changed = []
    for temp, original, item_number in temp_moves:
        target = original.parent / f"{item_number:03d}_{image_base_name(original)}"
        target = unique_destination(target)
        temp.rename(target)
        changed.append(purchase_image_info(target, item_number))

    return {"images": changed}


@app.get("/api/preview")
def preview(path: str) -> FileResponse:
    resolved = resolve_path(path)
    if not resolved.path.exists() or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(resolved.path, media_type=mimetypes.guess_type(resolved.path.name)[0])


@app.get("/api/download")
def download(path: str) -> FileResponse:
    resolved = resolve_path(path)
    if not resolved.path.exists() or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(resolved.path, filename=resolved.path.name)


@app.post("/api/folders")
def create_folder(payload: CreateFolderRequest) -> dict[str, object]:
    parent = resolve_path(payload.parentPath)
    if not parent.path.exists() or not parent.path.is_dir():
        raise HTTPException(status_code=404, detail="parent folder not found")
    name = safe_child_name(payload.name)
    target = parent.path / name
    if target.exists():
        raise HTTPException(status_code=409, detail="folder already exists")
    target.mkdir()
    return {"file": file_item(target)}


@app.post("/api/rename")
def rename(payload: RenameRequest) -> dict[str, object]:
    resolved = resolve_path(payload.path)
    if not resolved.path.exists():
        raise HTTPException(status_code=404, detail="path not found")
    name = safe_child_name(payload.newName, allow_file=resolved.path.is_file())
    target = resolved.path.parent / name
    if target.exists():
        raise HTTPException(status_code=409, detail="target already exists")
    resolved.path.rename(target)
    return {"file": file_item(target)}


@app.post("/api/delete")
def delete(payload: DeleteRequest) -> dict[str, object]:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_base = TRASH_DIR / stamp
    target_base.mkdir(parents=True, exist_ok=True)
    for raw_path in payload.paths:
        resolved = resolve_path(raw_path)
        if resolved.path.resolve() in {root.resolve() for root in ALLOWED_ROOTS.values()}:
            raise HTTPException(status_code=403, detail="cannot delete root")
        if not resolved.path.exists():
            continue
        target = unique_destination(target_base / resolved.path.name)
        shutil.move(str(resolved.path), str(target))
        moved.append(api_path(resolved.path))
    return {"deleted": moved}


@app.post("/api/move")
def move(payload: MoveRequest) -> dict[str, object]:
    destination = resolve_path(payload.destinationPath)
    if not destination.path.exists() or not destination.path.is_dir():
        raise HTTPException(status_code=404, detail="destination folder not found")
    copied: list[str] = []
    for raw_path in payload.paths:
        source = resolve_path(raw_path)
        if source.path.resolve() in {root.resolve() for root in ALLOWED_ROOTS.values()}:
            raise HTTPException(status_code=403, detail="cannot move root")
        if not source.path.exists():
            continue
        target = unique_destination(destination.path / source.path.name)
        if source.path.is_dir():
            if payload.operation == "copy":
                shutil.copytree(source.path, target)
            else:
                shutil.move(str(source.path), str(target))
        else:
            if payload.operation == "copy":
                shutil.copy2(source.path, target)
            else:
                shutil.move(str(source.path), str(target))
        copied.append(api_path(target))
    return {"paths": copied}


@app.post("/api/upload")
async def upload(
    parentPath: Annotated[str, Form()],
    file: Annotated[list[UploadFile], File()],
) -> dict[str, object]:
    parent = resolve_path(parentPath)
    if not parent.path.exists() or not parent.path.is_dir():
        raise HTTPException(status_code=404, detail="parent folder not found")
    saved: list[dict[str, object]] = []
    for upload_file in file:
        filename = file_services.safe_filename(upload_file.filename or "uploaded.bin")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(status_code=403, detail=f"unsupported file type: {suffix}")
        target = unique_destination(parent.path / filename)
        ensure_parent(target)
        with target.open("wb") as handle:
            while chunk := await upload_file.read(1024 * 1024):
                handle.write(chunk)
        saved.append(file_item(target))
    return {"uploaded": saved}


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
