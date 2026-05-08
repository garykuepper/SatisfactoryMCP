"""Paths and environment configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir

PKG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parents[1]

#: Candidate install locations, checked in order, when SATISFACTORY_DOCS is unset.
_DOCS_CANDIDATES = (
    r"G:\SteamLibrary\steamapps\common\Satisfactory",
    r"C:\Program Files (x86)\Steam\steamapps\common\Satisfactory",
    r"C:\Program Files\Epic Games\SatisfactoryEarlyAccess",
    r"D:\SteamLibrary\steamapps\common\Satisfactory",
    r"E:\SteamLibrary\steamapps\common\Satisfactory",
)
_DOCS_SUFFIX = Path("CommunityResources") / "Docs" / "en-US.json"


def docs_path() -> Path:
    """Locate Docs/en-US.json."""
    env = os.environ.get("SATISFACTORY_DOCS")
    if env:
        return Path(env)
    for base in _DOCS_CANDIDATES:
        candidate = Path(base) / _DOCS_SUFFIX
        if candidate.is_file():
            return candidate
    return Path(_DOCS_CANDIDATES[0]) / _DOCS_SUFFIX


def saves_root() -> Path:
    """Root of the save directory tree (contains one folder per Steam account)."""
    env = os.environ.get("SATISFACTORY_SAVES")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "FactoryGame" / "Saved" / "SaveGames"


def sidecar_path() -> Path:
    return REPO_ROOT / "sidecar" / "extract_save.py"


def data_dir() -> Path:
    return REPO_ROOT / "data"


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    """Machine-local regenerable cache.

    LOCALAPPDATA, not APPDATA: this must not roam.
    """
    d = Path(user_cache_dir("satisfactory-mcp", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def labels_dir() -> Path:
    """Factory names the player typed. NOT the cache, and NOT the repo.

    Deliberately separate from ``cache_dir``: cache_prune deletes everything under
    that tree, and a hand-written name is not regenerable. Out of the repo because it
    belongs to a save file, not to the source.
    """
    d = Path(user_data_dir("satisfactory-mcp", appauthor=False)) / "labels"
    d.mkdir(parents=True, exist_ok=True)
    return d
