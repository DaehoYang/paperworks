from __future__ import annotations

from pathlib import Path


GUI_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(__file__).resolve().parents[3]
MEETING_DIR = ROOT_DIR / "meeting"
PURCHASE_DIR = ROOT_DIR / "purchase"
PROJECTS_YML = ROOT_DIR / "projects.yml"
JOBS_DIR = GUI_DIR / "jobs"
TRASH_DIR = GUI_DIR / "trash"


def ensure_gui_dirs() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)


def assert_within_root(path: Path) -> Path:
    resolved = path.resolve()
    root = ROOT_DIR.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes repository root: {path}")
    return resolved


def repo_relative(path: Path) -> str:
    return str(assert_within_root(path).relative_to(ROOT_DIR.resolve()))


def resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return assert_within_root(path)
