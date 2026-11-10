from __future__ import annotations

import re
from pathlib import Path

import pytest

from facadekit.config import data_dir, models_dir, projects_dir
from facadekit.errors import FkError, http_status
from facadekit.ids import new_id, slugify, unique_slug


def test_fkerror_str_and_fields() -> None:
    err = FkError("X", "y")
    assert str(err) == "X: y"
    assert err.code == "X"
    assert err.message == "y"


def test_http_status_mapping() -> None:
    assert http_status("NOT_FOUND") == 404
    assert http_status("REVISION_CONFLICT") == 409
    assert http_status("INVALID_PROJECT") == 422
    assert http_status("SOMETHING_UNKNOWN") == 500


def test_data_dir_honours_env_and_creates_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "fk-data"
    monkeypatch.setenv("FACADEKIT_DATA", str(target))
    assert data_dir() == target
    assert (target / "projects").is_dir()
    assert (target / "models").is_dir()
    assert projects_dir() == target / "projects"
    assert models_dir() == target / "models"


def test_new_id_format() -> None:
    assert re.fullmatch(r"srf_[0-9a-f]{8}", new_id("srf"))


def test_slugify() -> None:
    assert slugify("Garage 2026!") == "garage-2026"
    assert slugify("???") == "project"
    assert slugify("  --Front Door--  ") == "front-door"
    assert len(slugify("a" * 80)) == 36


def test_unique_slug() -> None:
    assert unique_slug("Garage", {"garage", "garage-2"}) == "garage-3"
    assert unique_slug("Garage", set()) == "garage"
