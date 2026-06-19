from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.gui.services import files as file_services
from scripts.gui.services import jobs as job_services
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
    for case_dir in file_services.list_purchase_cases():
        if not case_dir.name[:1].isdigit():
            continue
        status = file_services.required_purchase_status(case_dir)
        missing = [label for label, matches in status.items() if not matches]
        infos = file_services.list_files(case_dir, recursive=True)
        purchase_cases.append(
            {
                "name": case_dir.name,
                "path": api_path(case_dir),
                "missing": missing,
                "required": {label: matches for label, matches in status.items()},
                "fileCount": len(infos),
                "updatedAt": utc_iso(case_dir.stat().st_mtime),
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
