from __future__ import annotations

import asyncio
import json
import base64
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode
import urllib.error
import urllib.request

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.documents.classifiers import (
    DOC_TYPE_LABELS,
    DOC_TYPES,
    classify_document,
    classify_document_content,
    missing_documents_for_doc_types,
    purchase_status_from_doc_types,
    required_documents_for_doc_types,
)
from scripts.documents.amounts import extract_pdf_text
from scripts.documents.purchase_scan import (
    DOCUMENT_EXTENSIONS as PURCHASE_DOCUMENT_EXTENSIONS,
    copy_file_info,
    document_types_for_file,
    file_sha256,
    file_info_entry,
    immediate_document_files,
    read_files_info,
    remove_file_info,
    rename_file_info,
    scan_purchase_root,
    update_file_info,
)
from scripts.documents.db import connect as connect_documents_db
from scripts.documents.db import purchase_workflow_for_case_dir
from scripts.gui.services import automation as automation_services
from scripts.gui.services import files as file_services
from scripts.gui.services import jobs as job_services
from scripts.gui.services import meeting as meeting_services
from scripts.gui.services import notifications as notification_services
from scripts.gui.services import paperwork as paperwork_services
from scripts.gui.services import projects as project_services
from scripts.gui.services.paths import MEETING_DIR, PURCHASE_DIR, ROOT_DIR, TRASH_DIR, assert_within_root, repo_relative


FRONTEND_DIST = ROOT_DIR / "scripts" / "gui" / "frontend" / "dist"
PURCHASE_DB = PURCHASE_DIR / "documents.sqlite3"
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
MEETING_INTERNAL_NAMES = {"meeting.sqlite3", "records.csv", "summary.csv"}


def jupyterhub_auth_enabled() -> bool:
    return os.environ.get("PAPERWORKS_REQUIRE_JUPYTERHUB_AUTH", "").lower() in {"1", "true", "yes", "on"}


def service_prefix() -> str:
    raw = os.environ.get("PAPERWORKS_BASE_PATH") or os.environ.get("JUPYTERHUB_SERVICE_PREFIX") or ""
    prefix = "/" + raw.strip("/")
    return "" if prefix == "/" else prefix


def url_path_join(*pieces: str) -> str:
    initial = pieces[0].startswith("/") if pieces else False
    final = pieces[-1].endswith("/") if pieces else False
    stripped = [piece.strip("/") for piece in pieces if piece]
    result = "/".join(piece for piece in stripped if piece)
    if initial:
        result = "/" + result
    if final and result and not result.endswith("/"):
        result += "/"
    return result or "/"


def service_path_without_prefix(path: str, prefix: str) -> str:
    normalized_prefix = prefix.rstrip("/")
    if normalized_prefix and path == normalized_prefix:
        return "/"
    if normalized_prefix and path.startswith(f"{normalized_prefix}/"):
        return path[len(normalized_prefix) :] or "/"
    return path


class StripServicePrefixMiddleware:
    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.prefix:
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path == self.prefix:
            response = RedirectResponse(f"{self.prefix}/")
            await response(scope, receive, send)
            return
        if path.startswith(f"{self.prefix}/"):
            scope = dict(scope)
            scope["root_path"] = self.prefix
            scope["path"] = path[len(self.prefix) :] or "/"
        await self.app(scope, receive, send)


class JupyterHubOAuthMiddleware:
    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix.rstrip("/")
        self.enabled = jupyterhub_auth_enabled()
        self.service_name = os.environ.get("JUPYTERHUB_SERVICE_NAME", "paperworks")
        self.client_id = os.environ.get("JUPYTERHUB_CLIENT_ID", f"service-{self.service_name}")
        self.api_token = os.environ.get("JUPYTERHUB_API_TOKEN", "")
        self.api_url = (os.environ.get("JUPYTERHUB_API_URL") or "http://127.0.0.1:59999/hub/api").rstrip("/")
        self.hub_base_url = os.environ.get("JUPYTERHUB_BASE_URL", "/")
        self.hub_prefix = url_path_join(self.hub_base_url, "hub")
        self.cookie_secret = os.environ.get("PAPERWORKS_COOKIE_SECRET") or self.api_token
        self.cookie_name = os.environ.get("PAPERWORKS_AUTH_COOKIE", f"paperworks-{self.service_name}-auth")
        self.state_cookie_name = f"{self.cookie_name}-state"
        self.cookie_max_age = int(os.environ.get("PAPERWORKS_AUTH_COOKIE_MAX_AGE", "28800"))
        self.required_scope = f"access:services!service={self.service_name}"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = scope.get("path") or "/"
        app_path = service_path_without_prefix(path, self.prefix)

        if app_path == "/api/health":
            await self.app(scope, receive, send)
            return
        if app_path == "/oauth_callback":
            response = await self.oauth_callback(request)
            await response(scope, receive, send)
            return

        missing = [name for name, value in {"JUPYTERHUB_API_TOKEN": self.api_token, "JUPYTERHUB_CLIENT_ID": self.client_id}.items() if not value]
        if missing:
            response = PlainTextResponse(f"Paperworks JupyterHub auth is enabled but missing: {', '.join(missing)}", status_code=503)
            await response(scope, receive, send)
            return

        user = await self.user_from_cookie(request)
        if user and self.user_allowed(user):
            scope = dict(scope)
            scope["paperworks_user"] = user
            await self.app(scope, receive, send)
            return

        response = self.login_redirect(request)
        await response(scope, receive, send)

    def callback_path(self) -> str:
        return url_path_join(self.prefix or "/", "oauth_callback")

    def current_path_with_query(self, request: Request) -> str:
        value = request.url.path
        if request.url.query:
            value += f"?{request.url.query}"
        return value

    def sign_payload(self, payload: dict[str, object]) -> str:
        if not self.cookie_secret:
            raise RuntimeError("cookie secret is not configured")
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(self.cookie_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def unsign_payload(self, value: str) -> dict[str, object] | None:
        if not value or "." not in value or not self.cookie_secret:
            return None
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(self.cookie_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = encoded + ("=" * (-len(encoded) % 4))
        try:
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def set_signed_cookie(self, response: RedirectResponse, name: str, payload: dict[str, object], max_age: int) -> None:
        response.set_cookie(
            name,
            self.sign_payload(payload),
            max_age=max_age,
            path=(self.prefix or "/"),
            httponly=True,
            secure=False,
            samesite="lax",
        )

    def login_redirect(self, request: Request) -> RedirectResponse:
        state_payload = {
            "nonce": secrets.token_urlsafe(24),
            "next_url": self.current_path_with_query(request),
            "exp": time.time() + 600,
        }
        state = self.sign_payload(state_payload)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_path(),
            "response_type": "code",
            "state": state,
        }
        response = RedirectResponse(f"{url_path_join(self.hub_prefix, 'api/oauth2/authorize')}?{urlencode(params)}")
        self.set_signed_cookie(response, self.state_cookie_name, {"state": state, "exp": time.time() + 600}, 600)
        return response

    async def oauth_callback(self, request: Request) -> PlainTextResponse | RedirectResponse:
        error = request.query_params.get("error")
        if error:
            return PlainTextResponse(f"JupyterHub OAuth failed: {error}", status_code=403)
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        state_cookie = self.unsign_payload(request.cookies.get(self.state_cookie_name, ""))
        state_payload = self.unsign_payload(state or "")
        if not code or not state or not state_cookie or state_cookie.get("state") != state or not state_payload:
            return PlainTextResponse("Invalid JupyterHub OAuth state.", status_code=403)
        if float(state_payload.get("exp") or 0) < time.time():
            return PlainTextResponse("Expired JupyterHub OAuth state.", status_code=403)

        try:
            token = await self.exchange_code(code)
            user = await self.user_for_token(token)
        except Exception as exc:
            return PlainTextResponse(f"JupyterHub OAuth verification failed: {exc}", status_code=403)
        if not user or not self.user_allowed(user):
            name = user.get("name") if isinstance(user, dict) else ""
            return PlainTextResponse(f"User is not allowed to access Paperworks: {name}", status_code=403)

        next_url = str(state_payload.get("next_url") or self.prefix or "/")
        response = RedirectResponse(next_url)
        response.delete_cookie(self.state_cookie_name, path=(self.prefix or "/"))
        self.set_signed_cookie(
            response,
            self.cookie_name,
            {"token": token, "name": user.get("name"), "exp": time.time() + self.cookie_max_age},
            self.cookie_max_age,
        )
        return response

    async def exchange_code(self, code: str) -> str:
        body = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.api_token,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.callback_path(),
            }
        ).encode("utf-8")

        def request_token() -> str:
            request = urllib.request.Request(
                f"{self.api_url}/oauth2/token",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            token = data.get("access_token")
            if not token:
                raise RuntimeError("OAuth token response did not include access_token")
            return str(token)

        return await asyncio.to_thread(request_token)

    async def user_for_token(self, token: str) -> dict[str, object]:
        def request_user() -> dict[str, object]:
            request = urllib.request.Request(f"{self.api_url}/user", headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"Hub user lookup failed with HTTP {exc.code}") from exc
            if not isinstance(data, dict):
                raise RuntimeError("Hub user lookup returned non-object JSON")
            return data

        return await asyncio.to_thread(request_user)

    async def user_from_cookie(self, request: Request) -> dict[str, object] | None:
        payload = self.unsign_payload(request.cookies.get(self.cookie_name, ""))
        if not payload or float(payload.get("exp") or 0) < time.time():
            return None
        token = str(payload.get("token") or "")
        if not token:
            return None
        try:
            return await self.user_for_token(token)
        except Exception:
            return None

    def user_allowed(self, user: dict[str, object]) -> bool:
        scopes = {str(scope) for scope in (user.get("scopes") or [])}
        return self.required_scope in scopes or "access:services" in scopes


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


class AutomationSettingsRequest(BaseModel):
    settings: dict[str, object]


class PurchaseProjectRequest(BaseModel):
    casePath: str
    projectId: str


class PurchaseDocTypeRequest(BaseModel):
    casePath: str
    path: str
    docType: str


def current_user(request: Request) -> dict[str, object]:
    user = request.scope.get("paperworks_user")
    if isinstance(user, dict):
        return user
    if not jupyterhub_auth_enabled():
        return {"name": os.environ.get("USER") or "local", "admin": True, "scopes": []}
    raise HTTPException(status_code=401, detail="not authenticated")


def user_role(user: dict[str, object]) -> str:
    return "admin" if bool(user.get("admin")) else "user"


def session_info(request: Request) -> dict[str, object]:
    user = current_user(request)
    role = user_role(user)
    return {
        "user": str(user.get("name") or ""),
        "role": role,
        "admin": role == "admin",
    }


def request_is_admin(request: Request) -> bool:
    return user_role(current_user(request)) == "admin"


def require_admin(request: Request) -> None:
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="admin access required")


def require_user(request: Request) -> None:
    current_user(request)


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
    try:
        meeting_rel = path.relative_to(MEETING_DIR)
    except ValueError:
        return True
    if path.name in MEETING_INTERNAL_NAMES:
        return False
    if meeting_rel.parts[:2] == ("receipt", "ocr_text") or meeting_rel.parts[:1] == ("ocr_text",):
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


def job_error_summary(job: job_services.Job) -> str:
    if job.status.get("state") != "failed":
        return ""
    stderr = job_services.read_log(job, "stderr.log", max_chars=4000)
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if lines:
        return lines[-1][-500:]
    stdout = job_services.read_log(job, "stdout.log", max_chars=4000)
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return lines[-1][-500:] if lines else "Job failed. Open Jobs for logs."


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


def write_purchase_metadata(case_dir: Path, metadata: dict[str, object]) -> None:
    path = case_dir / ".paperworks.yml"
    path.write_text(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False), encoding="utf-8")


def purchase_project_id(case_dir: Path) -> str:
    metadata = read_purchase_metadata(case_dir)
    workflow = metadata.get("workflow")
    if isinstance(workflow, dict):
        return str(workflow.get("project_id") or workflow.get("projectId") or "")
    return ""


def effective_purchase_project_id(case_dir: Path) -> str:
    return purchase_project_id(case_dir) or str(automation_services.read_settings().get("defaultProjectId") or "")


def set_purchase_project_id(case_dir: Path, project_id: str) -> None:
    valid_projects = {project.key for project in project_services.load_projects()} | {project.no for project in project_services.load_projects()}
    if project_id and project_id not in valid_projects:
        raise HTTPException(status_code=400, detail=f"unknown project id: {project_id}")
    metadata = read_purchase_metadata(case_dir)
    workflow = metadata.get("workflow")
    if not isinstance(workflow, dict):
        workflow = {}
    workflow["project_id"] = project_id
    metadata["workflow"] = workflow
    write_purchase_metadata(case_dir, metadata)


def visible_project_ids() -> set[str]:
    settings = automation_services.read_settings()
    raw = settings.get("visibleProjectIds")
    return {str(value) for value in raw} if isinstance(raw, list) else set()


def project_dicts() -> list[dict[str, str]]:
    visible = visible_project_ids()
    projects = project_services.load_projects()
    if visible:
        projects = [project for project in projects if project.key in visible or project.no in visible]
    return [project.__dict__ for project in projects]


def purchase_uploaded(case_dir: Path) -> bool:
    metadata = read_purchase_metadata(case_dir)
    workflow = metadata.get("workflow")
    if isinstance(workflow, dict) and workflow.get("uploaded") is True:
        return True
    status = metadata.get("status")
    return isinstance(status, dict) and status.get("uploaded") is True


def purchase_db_workflow(case_dir: Path) -> dict[str, object]:
    if not PURCHASE_DB.exists():
        return {}
    conn = connect_documents_db(PURCHASE_DB)
    try:
        row = purchase_workflow_for_case_dir(conn, case_dir)
        return dict(row) if row else {}
    finally:
        conn.close()


def purchase_workflow_label(doc_status: str, case_dir: Path) -> dict[str, object]:
    image_count = len(purchase_image_paths(case_dir))
    generated = purchase_generated(case_dir)
    uploaded = purchase_uploaded(case_dir)
    workflow = purchase_db_workflow(case_dir)
    items_status = str(workflow.get("items_status") or ("generated" if (case_dir / "items.xls").exists() else "pending"))
    items_label = {
        "generated": "items ready",
        "failed": "items failed",
        "pending": "items pending",
    }.get(items_status, f"items {items_status}")
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
        "itemsStatus": items_status,
        "itemsError": workflow.get("items_error") or "",
        "statusLabel": f"{doc_status} · {items_label} · {workflow_status}",
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


def editor_preview_allowed(resolved: ResolvedPath) -> bool:
    if resolved.root_key != "purchase" or not resolved.path.is_file():
        return False
    if resolved.path.suffix.lower() not in file_services.IMAGE_EXTENSIONS:
        return False
    return any(part.lower().startswith("img") for part in resolved.path.relative_to(PURCHASE_DIR).parts[:-1])


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


def direct_purchase_case_dir_for_file(path: Path) -> Path | None:
    purchase_root = PURCHASE_DIR.resolve()
    parent = path.parent.resolve()
    if parent == purchase_root or purchase_root not in parent.parents:
        return None
    if not parent.name[:1].isdigit():
        return None
    return parent


def classify_purchase_upload(path: Path) -> dict[str, object]:
    fallback = classify_document(path.name)
    classification = fallback
    source = "filename"
    if path.suffix.lower() == ".pdf":
        try:
            text = extract_pdf_text(path)
        except Exception:
            text = ""
        if text.strip():
            classification = classify_document_content(path.name, text, fallback)
            source = "pdf_text"
    return {
        "doc_type": classification.doc_type,
        "all_doc_types": list(classification.all_doc_types or (classification.doc_type,)),
        "classification": "auto",
        "classification_source": source,
        "confidence": classification.confidence,
        "reason": classification.reason,
        "document_number": classification.document_number,
        "item_code": classification.item_code,
        "source": "manual_upload",
        "source_filename": path.name,
        "sha256": file_sha256(path),
    }


def record_purchase_upload_info(path: Path) -> None:
    if path.name == "files_info.json":
        return
    case_dir = direct_purchase_case_dir_for_file(path)
    if not case_dir:
        return
    update_file_info(case_dir, path, classify_purchase_upload(path))


def purchase_doc_info(case_dir: Path, path: Path) -> dict[str, object]:
    files_info = read_files_info(case_dir)
    entry = file_info_entry(path, files_info)
    inferred_types = document_types_for_file(path, files_info)
    doc_type = str(entry.get("doc_type") or (inferred_types[0] if inferred_types else "unknown"))
    all_doc_types = entry.get("all_doc_types")
    if not isinstance(all_doc_types, list):
        all_doc_types = inferred_types or ([doc_type] if doc_type in DOC_TYPES else [])
    classification = entry.get("classification") or ("auto" if inferred_types else "")
    classification_source = entry.get("classification_source") or ("existing metadata/name" if inferred_types else "")
    reason = entry.get("reason") or ("existing files_info/sidecar/filename classification" if inferred_types else "")
    return {
        "name": path.name,
        "path": api_path(path),
        "docType": doc_type,
        "docTypeLabel": DOC_TYPE_LABELS.get(doc_type, "미분류"),
        "allDocTypes": [str(value) for value in all_doc_types],
        "classification": classification,
        "classificationSource": classification_source,
        "confidence": entry.get("confidence"),
        "reason": reason,
        "updatedAt": entry.get("updated_at") or utc_iso(path.stat().st_mtime),
    }


def set_purchase_doc_type(case_dir: Path, path: Path, doc_type: str) -> dict[str, object]:
    if doc_type != "unknown" and doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown document type: {doc_type}")
    all_doc_types = [doc_type] if doc_type in DOC_TYPES else []
    update_file_info(
        case_dir,
        path,
        {
            "doc_type": doc_type,
            "all_doc_types": all_doc_types,
            "classification": "manual",
            "classification_source": "user",
            "confidence": 1.0,
            "reason": "manual override",
            "source": "manual_upload",
            "source_filename": path.name,
            "sha256": file_sha256(path),
        },
    )
    return purchase_doc_info(case_dir, path)


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
app.add_middleware(StripServicePrefixMiddleware, prefix=service_prefix())
app.add_middleware(JupyterHubOAuthMiddleware, prefix=service_prefix())
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def start_collect_docs_action() -> dict[str, object]:
    reject_duplicate_action("collect_docs")
    job = job_services.start_job(
        "collect_docs",
        [sys.executable, "-u", "scripts/documents/run_daily.py"],
        metadata={"target": "purchase"},
        cwd=ROOT_DIR,
    )
    return action_job_response([job])


def start_generate_purchase_docs_action() -> dict[str, object]:
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


def start_upload_purchases_action() -> dict[str, object]:
    reject_duplicate_action("upload_purchases")
    grouped: dict[str, list[Path]] = {}
    skipped: list[dict[str, str]] = []
    for case in scan_purchase_root(PURCHASE_DIR):
        if not case.path.name[:1].isdigit():
            continue
        doc_status = purchase_status_from_doc_types(set(case.local_docs))
        if doc_status != "finished":
            skipped.append({"case": case.name, "reason": f"status={doc_status}"})
            continue
        if not purchase_generated(case.path):
            skipped.append({"case": case.name, "reason": "not generated"})
            continue
        if purchase_uploaded(case.path):
            skipped.append({"case": case.name, "reason": "already uploaded"})
            continue
        project_id = effective_purchase_project_id(case.path)
        if not project_id:
            skipped.append({"case": case.name, "reason": "missing project"})
            continue
        grouped.setdefault(project_id, []).append(case.path)
    jobs: list[job_services.Job] = []
    for project_id, case_dirs in grouped.items():
        jobs.append(
            job_services.start_job(
                "upload_purchases",
                paperwork_services.portal_command(case_dirs, project_id=project_id, step="fill-submit"),
                metadata={
                    "case_dirs": [repo_relative(path) for path in case_dirs],
                    "project_id": project_id,
                    "step": "fill-submit",
                },
                cwd=ROOT_DIR,
            )
        )
    if not jobs:
        detail = "No purchase cases are ready for upload."
        if skipped:
            detail += " Checked cases were skipped because they are uploaded, not finished/generated, or missing project."
        raise HTTPException(status_code=409, detail=detail)
    response = action_job_response(jobs)
    response["skipped"] = skipped
    return response


def start_process_receipts_action() -> dict[str, object]:
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


def start_send_meeting_mail_action() -> dict[str, object]:
    reject_duplicate_action("send_meeting_mail")
    settings = automation_services.read_settings()
    recipient = str(settings.get("meetingEmailRecipient") or "").strip()
    if not recipient:
        raise HTTPException(status_code=409, detail="Meeting email recipient is not set.")
    zip_paths = meeting_services.unsent_output_zips()
    if not zip_paths:
        raise HTTPException(status_code=409, detail="No unsent meeting zip files found.")
    jobs = [
        job_services.start_job(
            "send_meeting_mail",
            paperwork_services.send_meeting_mail_command(zip_path, recipient),
            metadata={"attachment": repo_relative(zip_path), "recipient": recipient},
            cwd=ROOT_DIR,
        )
        for zip_path in zip_paths
    ]
    return action_job_response(jobs)


AUTOMATION_ACTIONS = {
    "collect_docs": start_collect_docs_action,
    "generate_purchase_docs": start_generate_purchase_docs_action,
    "upload_purchases": start_upload_purchases_action,
    "process_receipts": start_process_receipts_action,
    "send_meeting_mail": start_send_meeting_mail_action,
}


def run_automation_scheduler() -> None:
    while True:
        for action, schedule, key in automation_services.due_actions():
            token = job_services.AUTOMATION_CONTEXT.set({"action": action, "schedule": schedule, "key": key})
            try:
                result = AUTOMATION_ACTIONS[action]()
                automation_services.record_run(action, schedule, key, ok=True, detail=json.dumps(result, ensure_ascii=False))
            except Exception as exc:
                detail = str(exc)
                automation_services.record_run(action, schedule, key, ok=False, detail=detail)
                try:
                    notification_services.send_automation_failure(action=action, schedule=schedule, key=key, detail=detail)
                except Exception:
                    pass
            finally:
                job_services.AUTOMATION_CONTEXT.reset(token)
        time.sleep(60)


@app.on_event("startup")
def start_automation_scheduler() -> None:
    thread = threading.Thread(target=run_automation_scheduler, name="paperworks-automation", daemon=True)
    thread.start()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/session")
def session(request: Request) -> dict[str, object]:
    return session_info(request)


@app.get("/api/files")
def list_files(request: Request) -> dict[str, object]:
    require_admin(request)
    roots = [{"name": key, "isDirectory": True, "path": f"/{key}", "updatedAt": utc_iso(path.stat().st_mtime)} for key, path in ALLOWED_ROOTS.items()]
    children: list[dict[str, object]] = []
    for root in ALLOWED_ROOTS.values():
        if root.exists():
            children.extend(collect_files(root)[1:])
    return {"files": roots + children}


@app.get("/api/dashboard")
def dashboard(request: Request) -> dict[str, object]:
    is_admin = request_is_admin(request)
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
        project_id = purchase_project_id(case.path)
        effective_project_id = effective_purchase_project_id(case.path)
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
                "projectId": project_id,
                "effectiveProjectId": effective_project_id,
            }
        )

    if not is_admin:
        return {
            "projects": [],
            "purchaseCases": purchase_cases,
            "meeting": {"items": []},
            "jobs": [],
        }

    meeting_status = {**meeting_services.status_summary(), "items": meeting_services.meeting_items()}

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
                "errorSummary": job_error_summary(job),
            }
        )

    return {
        "projects": project_dicts(),
        "purchaseCases": purchase_cases,
        "meeting": meeting_status,
        "jobs": recent_jobs,
    }


@app.get("/api/jobs")
def list_jobs(request: Request) -> dict[str, object]:
    require_admin(request)
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
                "errorSummary": job_error_summary(job),
            }
        )
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    require_admin(request)
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": {"id": job.id, **job.status, "command": job_services.command_for_job(job)}}


@app.get("/api/jobs/{job_id}/stdout")
def get_job_stdout(job_id: str, request: Request) -> dict[str, str]:
    require_admin(request)
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"text": job_services.read_log(job, "stdout.log")}


@app.get("/api/jobs/{job_id}/stderr")
def get_job_stderr(job_id: str, request: Request) -> dict[str, str]:
    require_admin(request)
    job = job_services.load_job(job_id)
    if not job.dir.exists():
        raise HTTPException(status_code=404, detail="job not found")
    return {"text": job_services.read_log(job, "stderr.log")}


@app.get("/api/automation-settings")
def get_automation_settings(request: Request) -> dict[str, object]:
    require_admin(request)
    return {"settings": automation_services.read_settings()}


@app.post("/api/automation-settings")
def update_automation_settings(payload: AutomationSettingsRequest, request: Request) -> dict[str, object]:
    require_admin(request)
    return {"settings": automation_services.write_settings(payload.settings)}


@app.get("/api/projects")
def get_projects(request: Request) -> dict[str, object]:
    require_admin(request)
    return {"projects": [project.__dict__ for project in project_services.load_projects()]}


@app.post("/api/purchase-project")
def update_purchase_project(payload: PurchaseProjectRequest, request: Request) -> dict[str, object]:
    require_admin(request)
    case_dir = resolve_purchase_case_path(payload.casePath)
    set_purchase_project_id(case_dir, payload.projectId)
    return {"casePath": api_path(case_dir), "projectId": payload.projectId}


@app.post("/api/actions/collect_docs")
def collect_docs(request: Request) -> dict[str, object]:
    require_admin(request)
    return start_collect_docs_action()


@app.post("/api/actions/generate_purchase_docs")
def generate_purchase_docs(request: Request) -> dict[str, object]:
    require_admin(request)
    return start_generate_purchase_docs_action()


@app.post("/api/actions/upload_purchases")
def upload_purchases(request: Request) -> dict[str, object]:
    require_admin(request)
    return start_upload_purchases_action()


@app.post("/api/actions/process_receipts")
def process_receipts(request: Request) -> dict[str, object]:
    require_admin(request)
    return start_process_receipts_action()


@app.post("/api/actions/send_meeting_mail")
def send_meeting_mail(request: Request) -> dict[str, object]:
    require_admin(request)
    return start_send_meeting_mail_action()


@app.get("/api/purchase-docs")
def purchase_docs(casePath: str, request: Request) -> dict[str, object]:
    require_user(request)
    case_dir = resolve_purchase_case_path(casePath)
    docs = [purchase_doc_info(case_dir, path) for path in immediate_document_files(case_dir)]
    return {"casePath": api_path(case_dir), "caseName": case_dir.name, "documents": docs}


@app.post("/api/purchase-docs/upload")
async def upload_purchase_docs(
    casePath: Annotated[str, Form()],
    file: Annotated[list[UploadFile], File()],
    request: Request,
) -> dict[str, object]:
    require_user(request)
    case_dir = resolve_purchase_case_path(casePath)
    saved: list[dict[str, object]] = []
    for upload_file in file:
        filename = file_services.safe_filename(upload_file.filename or "uploaded.bin")
        if filename == "files_info.json":
            raise HTTPException(status_code=403, detail="files_info.json is reserved")
        suffix = Path(filename).suffix.lower()
        if suffix not in PURCHASE_DOCUMENT_EXTENSIONS:
            raise HTTPException(status_code=403, detail=f"unsupported file type: {suffix}")
        target = unique_destination(case_dir / filename)
        with target.open("wb") as handle:
            while chunk := await upload_file.read(1024 * 1024):
                handle.write(chunk)
        record_purchase_upload_info(target)
        saved.append(purchase_doc_info(case_dir, target))
    return {"casePath": api_path(case_dir), "caseName": case_dir.name, "documents": saved}


@app.post("/api/purchase-docs/doc-type")
def update_purchase_doc_type(payload: PurchaseDocTypeRequest, request: Request) -> dict[str, object]:
    require_user(request)
    case_dir = resolve_purchase_case_path(payload.casePath)
    resolved = resolve_path(payload.path)
    if resolved.root_key != "purchase" or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    if resolved.path.parent.resolve() != case_dir.resolve():
        raise HTTPException(status_code=403, detail="document must be directly inside purchase case")
    return {"document": set_purchase_doc_type(case_dir, resolved.path, payload.docType)}


@app.get("/api/purchase-image-helper")
def purchase_image_helper(casePath: str, request: Request) -> dict[str, object]:
    require_user(request)
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
    request: Request,
) -> dict[str, object]:
    require_user(request)
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
def delete_purchase_image(payload: DeletePurchaseImageRequest, request: Request) -> dict[str, object]:
    require_user(request)
    case_dir = resolve_purchase_case_path(payload.casePath)
    image_path = resolve_purchase_image_path(case_dir, payload.path)
    archived_path = archive_purchase_image(image_path)
    return {"deleted": payload.path, "archived": api_path(archived_path)}


@app.post("/api/purchase-image-helper/reorder")
def reorder_purchase_images(payload: ReorderPurchaseImagesRequest, request: Request) -> dict[str, object]:
    require_user(request)
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
def preview(path: str, request: Request) -> FileResponse:
    resolved = resolve_path(path)
    if not request_is_admin(request) and not editor_preview_allowed(resolved):
        raise HTTPException(status_code=403, detail="admin access required")
    if not resolved.path.exists() or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(resolved.path, media_type=mimetypes.guess_type(resolved.path.name)[0])


@app.get("/api/download")
def download(path: str, request: Request) -> FileResponse:
    require_admin(request)
    resolved = resolve_path(path)
    if not resolved.path.exists() or not resolved.path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(resolved.path, filename=resolved.path.name)


@app.post("/api/folders")
def create_folder(payload: CreateFolderRequest, request: Request) -> dict[str, object]:
    require_admin(request)
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
def rename(payload: RenameRequest, request: Request) -> dict[str, object]:
    require_admin(request)
    resolved = resolve_path(payload.path)
    if not resolved.path.exists():
        raise HTTPException(status_code=404, detail="path not found")
    name = safe_child_name(payload.newName, allow_file=resolved.path.is_file())
    target = resolved.path.parent / name
    if target.exists():
        raise HTTPException(status_code=409, detail="target already exists")
    was_file = resolved.path.is_file()
    case_dir = direct_purchase_case_dir_for_file(resolved.path) if was_file else None
    resolved.path.rename(target)
    if case_dir and direct_purchase_case_dir_for_file(target) == case_dir:
        rename_file_info(case_dir, resolved.path.name, target.name)
    return {"file": file_item(target)}


@app.post("/api/delete")
def delete(payload: DeleteRequest, request: Request) -> dict[str, object]:
    require_admin(request)
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
        case_dir = direct_purchase_case_dir_for_file(resolved.path) if resolved.path.is_file() else None
        shutil.move(str(resolved.path), str(target))
        if case_dir:
            remove_file_info(case_dir, resolved.path.name)
        moved.append(api_path(resolved.path))
    return {"deleted": moved}


@app.post("/api/move")
def move(payload: MoveRequest, request: Request) -> dict[str, object]:
    require_admin(request)
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
        source_was_file = source.path.is_file()
        source_case_dir = direct_purchase_case_dir_for_file(source.path) if source_was_file else None
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
        target_case_dir = direct_purchase_case_dir_for_file(target) if target.is_file() else None
        if source_case_dir and source_was_file:
            if payload.operation == "copy":
                if target_case_dir:
                    copy_file_info(source_case_dir, source.path.name, target_case_dir, target.name)
            elif target_case_dir and source_case_dir == target_case_dir:
                rename_file_info(source_case_dir, source.path.name, target.name)
            else:
                if target_case_dir:
                    copy_file_info(source_case_dir, source.path.name, target_case_dir, target.name)
                remove_file_info(source_case_dir, source.path.name)
        elif target_case_dir and target.is_file():
            record_purchase_upload_info(target)
        copied.append(api_path(target))
    return {"paths": copied}


@app.post("/api/upload")
async def upload(
    parentPath: Annotated[str, Form()],
    file: Annotated[list[UploadFile], File()],
    request: Request,
) -> dict[str, object]:
    require_admin(request)
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
        record_purchase_upload_info(target)
        saved.append(file_item(target))
    return {"uploaded": saved}


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
