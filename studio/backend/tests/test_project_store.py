from __future__ import annotations

import os
from pathlib import Path

import pytest

from facadekit.errors import FkError
from facadekit.models.project import ProjectorSpec
from facadekit.store.project_store import ProjectStore, load_project_dir, save_project_dir


def _store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path)


def test_create_makes_folders(tmp_path: Path) -> None:
    project = _store(tmp_path).create("My Show", ProjectorSpec())
    folder = tmp_path / "my-show.fkproj"
    assert project.id == "my-show"
    assert project.revision == 0
    assert (folder / "project.json").is_file()
    for sub in ("media", "calibration", "renders"):
        assert (folder / sub).is_dir()
    assert (folder / "project.json").read_text(encoding="utf-8").endswith("\n")


def test_create_twice_gets_suffix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("Show", ProjectorSpec())
    assert store.create("Show", ProjectorSpec()).id.endswith("-2")


def test_create_empty_name(tmp_path: Path) -> None:
    with pytest.raises(FkError) as exc:
        _store(tmp_path).create("   ", ProjectorSpec())
    assert exc.value.code == "BAD_REQUEST"


def test_save_bumps_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project = store.create("Show", ProjectorSpec())
    saved = store.save(project.model_copy(update={"name": "Renamed"}))
    assert saved.revision == 1
    loaded = store.load("show")
    assert loaded.name == "Renamed"
    assert loaded.revision == 1


def test_stale_save_conflicts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project = store.create("Show", ProjectorSpec())
    store.save(project)
    with pytest.raises(FkError) as exc:
        store.save(project)
    assert exc.value.code == "REVISION_CONFLICT"


def test_traversal_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for bad in ("../etc", "", "Show", "a/b"):
        with pytest.raises(FkError) as exc:
            store.load(bad)
        assert exc.value.code == "NOT_FOUND"
    with pytest.raises(FkError) as exc:
        store.delete("../etc")
    assert exc.value.code == "NOT_FOUND"


def test_corrupt_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("Show", ProjectorSpec())
    (tmp_path / "show.fkproj" / "project.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(FkError) as exc:
        store.load("show")
    assert exc.value.code == "INVALID_PROJECT"


def test_folder_name_wins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("Show", ProjectorSpec())
    (tmp_path / "show.fkproj").rename(tmp_path / "other.fkproj")
    assert store.load("other").id == "other"


def test_list_newest_first_skips_corrupt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("Old", ProjectorSpec())
    store.create("New", ProjectorSpec())
    store.create("Bad", ProjectorSpec())
    (tmp_path / "bad.fkproj" / "project.json").write_text("{", encoding="utf-8")
    os.utime(tmp_path / "old.fkproj" / "project.json", (1_000_000_000, 1_000_000_000))
    os.utime(tmp_path / "new.fkproj" / "project.json", (1_700_000_000, 1_700_000_000))
    summaries = store.list()
    assert [s.id for s in summaries] == ["new", "old"]
    assert summaries[0].modified_at == "2023-11-14T22:13:20Z"
    assert summaries[0].name == "New"


def test_list_missing_root(tmp_path: Path) -> None:
    assert ProjectStore(tmp_path / "nope").list() == []


def test_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create("Show", ProjectorSpec())
    store.delete("show")
    assert not (tmp_path / "show.fkproj").exists()
    with pytest.raises(FkError) as exc:
        store.delete("show")
    assert exc.value.code == "NOT_FOUND"


def test_dir_roundtrip(tmp_path: Path) -> None:
    project = _store(tmp_path).create("Show", ProjectorSpec())
    folder = tmp_path / "copy.fkproj"
    save_project_dir(folder, project)
    assert load_project_dir(folder) == project.model_copy(update={"id": "copy"})
    assert [p.name for p in folder.iterdir()] == ["project.json"]
