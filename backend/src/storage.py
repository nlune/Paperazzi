from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from src.config import get_settings
from src.models import ProjectRecord

PROJECT_LOCKS: dict[str, threading.Lock] = {}
PROJECT_LOCKS_GUARD = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    with PROJECT_LOCKS_GUARD:
        if project_id not in PROJECT_LOCKS:
            PROJECT_LOCKS[project_id] = threading.Lock()
        return PROJECT_LOCKS[project_id]


def project_dir(project_id: str) -> Path:
    return get_settings().projects_dir / project_id


def project_json_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=True))


def load_project(project_id: str) -> ProjectRecord:
    lock = _project_lock(project_id)
    with lock:
        path = project_json_path(project_id)
        if not path.exists():
            raise FileNotFoundError(f"Unknown project: {project_id}")
        return ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))


def save_project(project: ProjectRecord) -> None:
    lock = _project_lock(project.project_id)
    with lock:
        atomic_write_text(project_json_path(project.project_id), project.model_dump_json(indent=2))


def mutate_project(project_id: str, mutator: Callable[[ProjectRecord], None]) -> ProjectRecord:
    lock = _project_lock(project_id)
    with lock:
        path = project_json_path(project_id)
        if not path.exists():
            raise FileNotFoundError(f"Unknown project: {project_id}")
        project = ProjectRecord.model_validate_json(path.read_text(encoding="utf-8"))
        mutator(project)
        atomic_write_text(path, project.model_dump_json(indent=2))
        return project
