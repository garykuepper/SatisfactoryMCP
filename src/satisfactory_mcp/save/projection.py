"""Invoke the sidecar, cache its projection, and group saves into worlds.

This is the ONLY module that knows a save parser exists. Everything downstream
consumes the plain-dict projection, which is why the whole test suite can run from a
committed JSON fixture with no game install.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

SCHEMA_VERSION = 7
_MEM: dict[str, dict] = {}
_MEM_ORDER: list[str] = []
_MEM_MAX = 3

#: .NET ticks at the Unix epoch, for converting saveDateTimeInTicks.
_TICKS_AT_EPOCH = 621_355_968_000_000_000
_TICKS_PER_SECOND = 10_000_000


class SaveError(RuntimeError):
    """The sidecar could not produce a projection."""


@dataclass
class World:
    """One game world, identified by the header's saveIdentifier."""

    world_id: str
    session_name: str
    saves: list[dict] = field(default_factory=list)

    @property
    def newest(self) -> dict:
        return max(self.saves, key=lambda s: s["mtime_ns"])

    @property
    def max_play_duration_s(self) -> int:
        return max((s.get("play_duration_s") or 0) for s in self.saves)

    def manual_saves(self) -> list[dict]:
        return [s for s in self.saves if "autosave" not in s["filename"].lower()]


def ticks_to_epoch_seconds(ticks: int | None) -> float | None:
    if not ticks:
        return None
    return (ticks - _TICKS_AT_EPOCH) / _TICKS_PER_SECOND


def _run_sidecar(args: list[str], timeout: float = 180.0) -> dict:
    cmd = [sys.executable, str(config.sidecar_path()), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            # DEVNULL, not inherit. capture_output only redirects stdout/stderr, so
            # without this the sidecar inherits the MCP server's stdin -- which is the
            # client's JSON-RPC pipe. Anything that touches it blocks for ever and can
            # steal bytes from the protocol stream.
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            text=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SaveError(f"sidecar timed out after {timeout}s") from exc
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        err = proc.stderr.decode("utf-8", errors="replace")[-600:]
        raise SaveError(f"sidecar produced no output (exit {proc.returncode}): {err}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SaveError(f"sidecar emitted invalid JSON: {out[:200]}") from exc
    if isinstance(payload, dict) and "error" in payload:
        raise SaveError(f"{payload['error']}: {payload.get('detail', '')}")
    return payload


def scan_saves(root: str | Path | None = None) -> dict:
    """Header-only scan of the save tree. One subprocess, ~0.17 s for 63 files."""
    r = Path(root) if root else config.saves_root()
    if not r.exists():
        return {"root": str(r), "saves": [], "unsupported": [], "missing_root": True}
    return _run_sidecar(["--list", str(r)])


def list_worlds(root: str | Path | None = None) -> tuple[list[World], list[dict]]:
    """Group saves into worlds by ``save_identifier``.

    Verified stable across all 28 parseable saves in the reference directory.
    Returns (worlds newest-first, unsupported files with reasons).
    """
    scan = scan_saves(root)
    worlds: dict[str, World] = {}
    for s in scan.get("saves", ()):
        wid = s.get("save_identifier") or f"session:{s.get('session_name')}"
        w = worlds.get(wid)
        if w is None:
            w = worlds[wid] = World(world_id=wid, session_name=s.get("session_name") or "?")
        w.saves.append(s)
    ordered = sorted(worlds.values(), key=lambda w: w.newest["mtime_ns"], reverse=True)
    return ordered, list(scan.get("unsupported", ()))


def resolve_save(
    path: str | Path | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
) -> dict:
    """Pick a save header: explicit path, else newest in the named/only world."""
    if path:
        p = Path(path)
        if not p.is_file():
            raise SaveError(f"save not found: {p}")
        return _run_sidecar([str(p), "--header-only"])["header"]

    worlds, _ = list_worlds()
    if not worlds:
        raise SaveError(
            f"no readable saves under {config.saves_root()} "
            "(set SATISFACTORY_SAVES if they live elsewhere)"
        )
    chosen = None
    if world:
        needle = world.casefold()
        for w in worlds:
            if needle in (w.world_id.casefold(), w.session_name.casefold()):
                chosen = w
                break
        if chosen is None:
            names = ", ".join(f"{w.session_name!r}" for w in worlds)
            raise SaveError(f"no world matching {world!r}; known worlds: {names}")
    else:
        chosen = worlds[0]

    pool = chosen.manual_saves() if prefer_manual else chosen.saves
    if not pool:
        pool = chosen.saves
    return max(pool, key=lambda s: s["mtime_ns"])


def _cache_key(header: dict) -> str:
    """Identity of a parsed save.

    mtime_ns and size are essential: autosaves are rewritten IN PLACE under the same
    filename every ~5 minutes, so path alone would serve a stale world.
    """
    raw = "|".join(
        [
            header["path"],
            str(header["mtime_ns"]),
            str(header["size"]),
            str(SCHEMA_VERSION),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_projection(
    path: str | Path | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
    refresh: bool = False,
) -> dict:
    """Return the projection for a save, using the two-tier cache.

    Parsing costs ~4 s; a cache hit is ~1 ms.
    """
    header = resolve_save(path, world, prefer_manual)
    key = _cache_key(header)

    if not refresh:
        hit = _MEM.get(key)
        if hit is not None:
            return hit
        disk = config.cache_dir() / f"save-{key}.pkl"
        if disk.is_file():
            try:
                payload = pickle.loads(disk.read_bytes())
                _remember(key, payload)
                return payload
            except Exception:
                disk.unlink(missing_ok=True)  # corrupt or stale pickle format

    payload = _run_sidecar([header["path"]])
    if payload.get("schema_version") != SCHEMA_VERSION:
        payload.setdefault("warnings", []).append(
            f"sidecar schema {payload.get('schema_version')} != expected {SCHEMA_VERSION}"
        )
    _remember(key, payload)
    try:
        (config.cache_dir() / f"save-{key}.pkl").write_bytes(pickle.dumps(payload))
        # Prune on write, because autosaves rotate every ~5 minutes and each one is a
        # new cache key: without this the directory grows by ~500 kB per autosave for
        # ever. Globbing a dozen files is far cheaper than the 4 s parse we just did.
        prune_cache()
    except OSError:
        pass  # cache is an optimisation, never a requirement
    return payload


def _remember(key: str, payload: dict) -> None:
    _MEM[key] = payload
    if key in _MEM_ORDER:
        _MEM_ORDER.remove(key)
    _MEM_ORDER.append(key)
    while len(_MEM_ORDER) > _MEM_MAX:
        _MEM.pop(_MEM_ORDER.pop(0), None)


def prune_cache(keep: int = 12) -> int:
    """Drop all but the newest ``keep`` cached projections."""
    files = sorted(
        config.cache_dir().glob("save-*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed
