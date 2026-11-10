from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from facadekit.errors import BAD_REQUEST, INVALID_PROJECT, NOT_FOUND, REVISION_CONFLICT, FkError
from facadekit.ids import unique_slug
from facadekit.models.project import Project, ProjectorSpec, parse_project

PROJECT_SUFFIX = ".fkproj"
PROJECT_FILE = "project.json"
SUBFOLDERS = ("media", "calibration", "renders")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ProjectSummary(BaseModel):
    id: str
    name: str
    modified_at: str


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> Any:
    try:
        return json.loads((path / PROJECT_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FkError(NOT_FOUND, f"project folder {path.name} has no {PROJECT_FILE}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FkError(INVALID_PROJECT, f"{PROJECT_FILE}: {exc}") from exc


def load_project_dir(path: Path) -> Project:
    project = parse_project(_read_json(path))
    folder_id = path.name.removesuffix(PROJECT_SUFFIX)
    if project.id != folder_id:
        project = project.model_copy(update={"id": folder_id})
    return project


def _replace(src: str, dst: Path) -> None:
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def save_project_dir(path: Path, project: Project) -> None:
    path.mkdir(parents=True, exist_ok=True)
    text = json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _replace(tmp, path / PROJECT_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    def _path(self, project_id: str) -> Path:
        return self._root / f"{project_id}{PROJECT_SUFFIX}"

    def _ids(self) -> set[str]:
        if not self._root.is_dir():
            return set()
        return {
            p.name.removesuffix(PROJECT_SUFFIX)
            for p in self._root.iterdir()
            if p.is_dir() and p.name.endswith(PROJECT_SUFFIX)
        }

    def list(self) -> list[ProjectSummary]:
        entries: list[tuple[float, ProjectSummary]] = []
        for project_id in self._ids():
            folder = self._path(project_id)
            try:
                data = _read_json(folder)
                name = data["name"]
                if not isinstance(name, str):
                    continue
                mtime = (folder / PROJECT_FILE).stat().st_mtime
            except (FkError, OSError, KeyError, TypeError):
                continue
            entries.append(
                (mtime, ProjectSummary(id=project_id, name=name, modified_at=_iso(mtime)))
            )
        entries.sort(key=lambda e: (e[0], e[1].id), reverse=True)
        return [summary for _, summary in entries]

    def create(self, name: str, projector: ProjectorSpec) -> Project:
        name = name.strip()
        if not name:
            raise FkError(BAD_REQUEST, "project name must not be empty")
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            project_id = unique_slug(name, self._ids())
            folder = self._path(project_id)
            folder.mkdir()
            for sub in SUBFOLDERS:
                (folder / sub).mkdir()
            project = Project(id=project_id, name=name, revision=0, projector=projector)
            save_project_dir(folder, project)
            return project

    def project_dir(self, project_id: str) -> Path:
        if not _ID_RE.match(project_id):
            raise FkError(NOT_FOUND, f"project {project_id!r} not found")
        folder = self._path(project_id)
        if not folder.is_dir():
            raise FkError(NOT_FOUND, f"project {project_id!r} not found")
        return folder

    def load(self, project_id: str) -> Project:
        return load_project_dir(self.project_dir(project_id))

    def save(self, project: Project) -> Project:
        with self._lock:
            folder = self.project_dir(project.id)
            stored = load_project_dir(folder)
            if project.revision != stored.revision:
                raise FkError(
                    REVISION_CONFLICT,
                    f"project {project.id!r} is at revision {stored.revision}, "
                    f"got {project.revision}",
                )
            saved = project.model_copy(update={"revision": stored.revision + 1})
            save_project_dir(folder, saved)
            return saved

    def delete(self, project_id: str) -> None:
        with self._lock:
            shutil.rmtree(self.project_dir(project_id))
