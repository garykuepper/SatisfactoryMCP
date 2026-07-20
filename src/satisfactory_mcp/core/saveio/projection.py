"""Invoke the sidecar, cache its projection, and group saves into worlds.

This is the ONLY module that knows a save parser exists. Everything downstream
consumes the plain-dict projection, which is why the whole test suite can run from a
committed JSON fixture with no game install.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ... import config
from .. import atomic

#: Bumped whenever the projection's shape changes, and part of the disk cache key below, so
#: every pickle written by an older schema misses rather than being served without its new
#: fields. 12 added placement yaw and belt splines; 13 added fluid pipe splines and the belt
#: attachments -- the splitters and mergers a run passes through; 14 added a fourth column to
#: a pipe segment, the index of its own actor, which joins the drawn pipe to the connection
#: graph that has been in ``graph["material"]`` since schema 11 and is what lets flow
#: direction be inferred; 15 added the SPLINE TANGENTS to both route keys, so a curved belt or
#: a pipe elbow can be drawn as the curve it was built as rather than as the chords between its
#: corners, and a ``storage`` key -- the containers and fluid buffers, with what is in each one.
#: 16 is the first bump that CORRECTS existing keys rather than adding new ones, so a pickle
#: written under 15 is not merely thinner than what this code expects, it disagrees with it:
#: ``inventories`` bucketed eight containers' contents as unspendable machine buffers, and a
#: placement whose rotation would not read claimed to be axis-aligned instead of saying nothing.
#: 17 added ``power`` -- the poles, and the endpoints of every wire between them. Geometry
#: only, and that is the shape of the change: the CONNECTIVITY has been in ``graph["power"]``
#: since schema 11, so the new key is that edge list's positional twin (``wires[i]`` is the
#: span of ``graph["power"][i]``) rather than a second, disagreeable copy of who is wired to
#: whom.
#: 18 added ``crates`` -- the death and dismantle crates lying on the ground, and what is in
#: each one. Additive and its own key rather than more ``storage`` rows: a container is
#: infrastructure the player built and a crate is a situation the player got into, and a
#: pickle written under 17 simply has no such key rather than disagreeing about one.
SCHEMA_VERSION = 18
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


def _child_env() -> dict[str, str]:
    """This process's environment, with the source tree put in front on PYTHONPATH.

    Merged over ``os.environ`` rather than replacing it, because the extractor is a normal
    Python program: it wants the same PATH, the same TEMP and the same console encoding. The
    one thing made explicit is where it imports from -- ``satisfactory_mcp`` for the extractor
    module itself and ``pioneersav`` for the parser -- so that a checkout always runs its own
    source no matter what an inherited PYTHONPATH says.
    """
    env = dict(os.environ)
    root = config.source_root()
    if root is not None:
        inherited = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{root}{os.pathsep}{inherited}" if inherited else str(root)
    return env


#: How much of the child's stderr is carried, in characters, taken from the END. The end is
#: where a traceback's exception line is and where the last thing the parser complained about
#: is; the beginning is a page of notes about a file that then parsed fine.
STDERR_TAIL_CHARS = 600


def _because(tail: str) -> str:
    """The stderr tail as a clause to hang on a message, or nothing at all.

    A separate function because it is appended at three raise sites and an empty stderr must
    add no punctuation to any of them -- a message ending in ``: `` reads as a truncated
    error rather than as an error with nothing more to say.
    """
    return f" -- sidecar stderr: {tail}" if tail else ""


def _run_sidecar(args: list[str], timeout: float = 180.0) -> dict:
    """Run the extractor in a child process and return its payload, or raise ``SaveError``.

    **Both of the child's channels are evidence, and this function used to read each of them
    in exactly one case.** stdout is the payload; stderr is the parser saying what it skipped
    and the interpreter printing a traceback. Before this, stderr was read only when stdout
    was empty -- so a crash whose traceback was on stderr and whose exception NAME was on
    stdout reported ``AttributeError:`` and nothing else -- and the exit code was read only
    in that same case, so a child that wrote a payload and then died was believed.

    The four outcomes, in the order they are decided:

    * **nothing on stdout** -- the sidecar never got going. Exit code and stderr tail.
    * **stdout carries an ``error`` key** -- the child's own designed refusal, an unreadable
      save or a bad argument. Its own words, plus the stderr tail, which is where the
      traceback for an *unexpected* exception is.
    * **stdout parses and the exit code is non-zero** -- the child died after writing. The
      payload is of unknown completeness and is refused rather than cached.
    * **stdout parses and the exit was clean** -- the payload, with any stderr folded into
      its ``warnings``.
    """
    # ``-m``, not a file path: the child then imports the extractor exactly the way this
    # process was imported, so there is no second copy of the code to drift out of date.
    cmd = [sys.executable, "-m", config.EXTRACTOR_MODULE, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            env=_child_env(),
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
    tail = proc.stderr.decode("utf-8", errors="replace")[-STDERR_TAIL_CHARS:].strip()
    if not out:
        raise SaveError(f"sidecar produced no output (exit {proc.returncode}){_because(tail)}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SaveError(f"sidecar emitted invalid JSON: {out[:200]}{_because(tail)}") from exc

    # The child's own refusal, which is the one failure it is designed to have: `main` writes
    # `{"error": ..., "detail": ...}` and exits non-zero for an unreadable save, and for an
    # unexpected exception it ALSO writes the traceback to stderr. That traceback used to be
    # dropped on the floor, so "AttributeError: " was the whole of what a reader got.
    if isinstance(payload, dict) and "error" in payload:
        raise SaveError(f"{payload['error']}: {payload.get('detail', '')}{_because(tail)}")

    # A non-zero exit with clean JSON on stdout, which nothing checked until now. It means
    # the child died AFTER writing a payload -- a crash in the interpreter's own shutdown, a
    # MemoryError past the final `json.dump`, a kill from outside -- and the payload is then
    # of unknown completeness. Serving it would cache a half-read world under a key that says
    # it is the whole one, which is exactly the failure the schema in `_cache_key` exists to
    # prevent by a different route.
    if proc.returncode != 0:
        raise SaveError(
            f"sidecar exited {proc.returncode} after writing a payload, so what it wrote "
            f"cannot be trusted{_because(tail)}"
        )

    # It worked, and it still had something to say. `extract` sends the parser's own notes to
    # stderr on purpose -- what pioneersav skipped, which conveyor chain would not decode --
    # so that stdout stays parseable and so that the two parsers' payloads could not differ
    # over a diagnostic. Those notes were then read by nobody at all, which is a different
    # thing from keeping them out of the projection. They ride here instead, in the key the
    # projection already has for exactly this.
    #
    # ONE entry rather than one per line: this is the tail of a truncated stream, so its first
    # line is very likely half a line, and splitting it would publish a fragment as though it
    # were a note somebody wrote.
    if tail and isinstance(payload, dict):
        payload.setdefault("warnings", []).append(
            f"the sidecar wrote to stderr and the parse still succeeded; "
            f"last {STDERR_TAIL_CHARS} characters: {tail}"
        )
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
                # Corrupt, or a stale pickle format, or -- the case that made this cache
                # directory a shared one -- a file `prune_cache` deleted between the
                # `is_file` above and the read. The unlink is itself best-effort for the
                # same reason: on Windows it raises `PermissionError` while any other
                # process holds the file open, and this used to be the one line in the
                # whole read path that could take a caller down over a cache.
                try:
                    disk.unlink(missing_ok=True)
                except OSError:
                    pass

    payload = _run_sidecar([header["path"]])
    if payload.get("schema_version") != SCHEMA_VERSION:
        payload.setdefault("warnings", []).append(
            f"sidecar schema {payload.get('schema_version')} != expected {SCHEMA_VERSION}"
        )
    _remember(key, payload)
    try:
        # `atomic.write_bytes`, not `Path.write_bytes`, because this directory has more than
        # one writer: the web server, any CLI invocation and -- under `pytest-xdist` -- a
        # test worker per core, all of which resolve the same newest save and miss the same
        # key at the same moment. A plain write is create-then-fill, so a concurrent reader
        # gets a prefix of a pickle; `core/atomic.py` argues the whole case.
        atomic.write_bytes(config.cache_dir() / f"save-{key}.pkl", pickle.dumps(payload))
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
    """Drop all but the newest ``keep`` cached projections.

    **Every filesystem call here is best-effort, because another process is deleting the
    same files.** The directory is shared by the server, by the CLI and by a test worker per
    core, all of which prune on every write, and the folder sits at exactly ``keep`` entries
    in normal use -- so a prune racing another prune is the ordinary case rather than the
    unlucky one. The ``unlink`` was already guarded; the ``stat`` inside the sort key was
    not, and a file that vanished between the glob and the sort raised ``FileNotFoundError``
    out of ``sorted`` -- from the ``cache_prune`` tool, which calls this directly and has no
    outer guard to swallow it.

    A file whose ``stat`` fails sorts as if it were infinitely old. It is a file this call
    can no longer see, so ranking it last means the loop below tries to delete it and finds
    it already gone, which is exactly what happened.
    """

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return float("-inf")

    try:
        files = sorted(config.cache_dir().glob("save-*.pkl"), key=_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed
