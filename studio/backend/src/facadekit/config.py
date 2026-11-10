from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Return the data directory, creating it with projects/ and models/ if missing.

    Reads FACADEKIT_DATA on every call (no caching); defaults to ~/FacadeKit.
    """
    env = os.environ.get("FACADEKIT_DATA")
    path = Path(env).expanduser() if env else Path.home() / "FacadeKit"
    path.mkdir(parents=True, exist_ok=True)
    (path / "projects").mkdir(exist_ok=True)
    (path / "models").mkdir(exist_ok=True)
    return path


def projects_dir() -> Path:
    return data_dir() / "projects"


def models_dir() -> Path:
    return data_dir() / "models"
