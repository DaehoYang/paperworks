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
    start_date: str = ""
    end_date: str = ""

    @property
    def label(self) -> str:
        dates = f" ({self.start_date} - {self.end_date})" if self.start_date or self.end_date else ""
        return f"{self.no} - {self.name}{dates}"


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
        start_date = str(value.get("start_date") or value.get("start") or value.get("period_start") or "")
        end_date = str(value.get("end_date") or value.get("end") or value.get("period_end") or "")
        projects.append(Project(key=str(key), no=no, name=name, start_date=start_date, end_date=end_date))
    return sorted(projects, key=lambda item: item.no)


def project_options() -> dict[str, Project]:
    return {project.label: project for project in load_projects()}
