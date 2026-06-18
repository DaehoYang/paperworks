from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .paths import PROJECTS_YML


@dataclass(frozen=True)
class Project:
    key: str
    no: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.no} - {self.name}"


def load_projects(path: Path = PROJECTS_YML) -> list[Project]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_projects = data.get("projects") or {}
    if not isinstance(raw_projects, dict):
        return []
    projects: list[Project] = []
    for key, value in raw_projects.items():
        if not isinstance(value, dict):
            continue
        no = str(value.get("no") or key)
        name = str(value.get("name") or "")
        projects.append(Project(key=str(key), no=no, name=name))
    return sorted(projects, key=lambda item: item.no)


def project_options() -> dict[str, Project]:
    return {project.label: project for project in load_projects()}
