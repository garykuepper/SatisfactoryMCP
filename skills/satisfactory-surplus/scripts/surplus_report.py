"""How much surplus does each build item need? Measured from one world's save history.

Reads every snapshot of the world it can find (the save folder, searched recursively, plus
any extra folders in SATISFACTORY_SAVE_HISTORY, os.pathsep-separated: backups, a bot's
copies...), finds the play sessions, and reports per item how fast stock fell during the worst
session, in play-hours (the dedicated server is on-demand, so the clock only runs while
someone plays). Recommends a surplus rate: the worst net drain, or a default of N stacks per
play hour when there is no history, whichever is larger.

READ-ONLY: saves are copied to a temp dir and parsed there. Nothing is ever written next to
a save. Saves and Docs are found the way the planner finds them (SATISFACTORY_SAVES /
SATISFACTORY_DOCS, else the Windows defaults). From the satisfactory-mcp checkout:

  uv run python skills/satisfactory-surplus/scripts/surplus_report.py [--world NAME]
      [--item X ...] [--sessions] [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))  # repo checkout's src/
from satisfactory_mcp import config  # noqa: E402

STACK = {"SS_ONE": 1, "SS_SMALL": 50, "SS_MEDIUM": 100, "SS_BIG": 200, "SS_HUGE": 500}
SESSION_GAP_H = 3.0  # wall-clock gap between snapshots that starts a new session
MIN_SESSION_PLAY_H = 0.5  # shorter sessions are too noisy to rate
# ponytail: per-mesh-piece belt/pipe cost is from Docs mMeshLength, not checked in game;
# calibrate against one belt's in-game cost. Power lines are charged one Cable each (no length).
MESH_M = 2.0

WORKER = """
import sys, json, math
from satisfactory_mcp.interfaces.mcp.app import game
from satisfactory_mcp.domain.world.state import load_state
from satisfactory_mcp.domain.world.conduits import _length_m
from satisfactory_mcp.core.saveio import rows
g = game(); st = load_state(g, path=sys.argv[1]); h = st.header
# Belts and pipes cost their recipe once per 2 m mesh piece (Docs mMeshLength = 200 cm),
# so charge them by drawn length; everything else is one recipe per placed piece.
units = dict(st.built_counts)
for it in (rows.iter_belt_segments, rows.iter_pipe_segments):
    for seg in it(st.projection):
        if seg.cls in units:
            units[seg.cls] += math.ceil(_length_m(seg.points, seg.spans) / MESH_M) - 1
built = {}
for cls, n in units.items():
    b = g.buildings.get(cls)
    for f in (b.build_cost if b else ()):
        name = g.item_name(f.item); built[name] = built.get(name, 0) + f.amount * n
print(json.dumps({"play_s": h["play_duration_s"], "ticks": h["save_datetime_ticks"],
                  "stock": {g.item_name(k): v for k, v in st.stock().items()}, "built": built}))
""".replace("MESH_M", str(MESH_M))


def item_table() -> dict[str, dict]:
    """name -> {stack, build, se}: stack size, used by the build gun, Space Elevator part."""
    raw = open(config.docs_path(), "rb").read()
    docs = json.loads(raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"))
    classes = [c for grp in docs for c in grp.get("Classes", [])]
    by_class = {c["ClassName"]: c for c in classes if "mStackSize" in c}
    build_classes = set()
    for c in classes:
        if "BuildGun" in c.get("mProducedIn", "") and "mIngredients" in c:
            for part in c["mIngredients"].split("Desc_")[1:]:
                build_classes.add("Desc_" + part.split("'")[0].split(".")[-1])
    out = {}
    for cls, c in by_class.items():
        name = c.get("mDisplayName")
        if name:
            out[name] = {
                "stack": STACK.get(c["mStackSize"], 0),
                "build": cls in build_classes,
                "se": cls.startswith("Desc_SpaceElevatorPart"),
            }
    return out


def save_dirs() -> list[str]:
    extra = os.environ.get("SATISFACTORY_SAVE_HISTORY", "")
    return [str(config.saves_root())] + [d for d in extra.split(os.pathsep) if d]


def newest_world() -> str:
    """Session name of the newest save, from its filename: <world>.sav or <world>_<kind>.sav."""
    paths = [p for d in save_dirs()[:1] for p in glob.glob(os.path.join(d, "**", "*.sav"), recursive=True)]
    if not paths:
        sys.exit(f"no .sav files under {save_dirs()[0]}; set SATISFACTORY_SAVES")
    return world_of(Path(max(paths, key=os.path.getmtime)).stem)


def world_of(stem: str) -> str:
    """Base_autosave_2_continue -> Base: the suffixes stack."""
    return re.sub(r"(_(autosave_\d+|manual|current|continue|\d+))+$", "", stem)


def is_world_file(name: str, world: str) -> bool:
    """A prefix match would let 'Base' claim 'BaseTwo', so the name must end at '_' or
    '.sav'; a leading '<stamp>_' (a bot's copies) is allowed."""
    return bool(re.search(r"(?:^|_)" + re.escape(world) + r"(?:_[^/\\]*)?\.sav$", name))


def world_files(world: str) -> list[str]:
    """Every .sav of this world in the save dirs."""
    return sorted({p for d in save_dirs() for p in glob.glob(os.path.join(d, "**", "*.sav"), recursive=True)
                   if is_world_file(os.path.basename(p), world)})


def snapshots(world: str) -> list[dict]:
    paths = world_files(world)
    snaps: dict[int, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(paths):
            # Clean name in a temp dir: never parse in place, and the bot's timestamp prefix
            # confuses world inference. One process per save: the planner caches per world.
            copy = os.path.join(tmp, f"{world}_snap{i:03d}.sav")
            shutil.copy2(p, copy)
            r = subprocess.run([sys.executable, "-c", WORKER, copy], capture_output=True, text=True, cwd=tmp)
            if r.returncode != 0 or not r.stdout.strip():
                print(f"skip {p}: {r.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
            else:
                s = json.loads(r.stdout.strip().splitlines()[-1])
                s["wall"] = dt.datetime(1, 1, 1) + dt.timedelta(microseconds=int(s["ticks"]) // 10)
                snaps[int(s["play_s"])] = s  # same play time = same state, keep one
            os.remove(copy)
    return [snaps[k] for k in sorted(snaps)]


def sessions(snaps: list[dict]) -> list[tuple[dict, dict]]:
    """(stock before, stock after) per session. The clock stops between sessions, so the
    last snapshot of one session is the starting stock of the next."""
    ends = []
    for a, b in zip(snaps, snaps[1:]):
        if (b["wall"] - a["wall"]).total_seconds() / 3600 > SESSION_GAP_H and b["play_s"] - a["play_s"] > 60:
            ends.append(a)
    ends.append(snaps[-1])
    pairs = list(zip(ends, ends[1:]))
    return [(a, b) for a, b in pairs if (b["play_s"] - a["play_s"]) / 3600 >= MIN_SESSION_PLAY_H]


def session_row(a: dict, b: dict, name: str) -> dict:
    """One item over one session: storage change, material placed in the world (net of
    dismantles), and factory net = storage change + built (what production and hand-feeding
    did on their own)."""
    stock = b["stock"].get(name, 0) - a["stock"].get(name, 0)
    built = b.get("built", {}).get(name, 0) - a.get("built", {}).get(name, 0)
    return {"play_h": round((b["play_s"] - a["play_s"]) / 3600, 2), "stock_change": round(stock),
            "built": round(built), "factory_net": round(stock + built)}


def building_drain(r: dict) -> float:
    """The part of a session's stock fall that went into building. A fall with nothing built
    is hand-feeding; building that storage still rose through means production kept up."""
    return max(0.0, min(-r["stock_change"], r["built"]))


def report(world: str, only: list[str], stacks_per_h: float) -> dict:
    items = item_table()
    snaps = snapshots(world)
    sess = sessions(snaps)
    now = snaps[-1]["stock"]
    rows = []
    names = only or sorted(n for n, i in items.items() if i["build"] or i["se"])
    for name in names:
        info = items.get(name, {"stack": 0, "build": False, "se": False})
        per = [session_row(x, y, name) for x, y in sess]
        seen = any(x["stock"].get(name) or y["stock"].get(name) for x, y in sess)
        worst = max([0.0] + [-r["stock_change"] / r["play_h"] for r in per])
        build_drain = max([0.0] + [building_drain(r) / r["play_h"] for r in per])
        default = stacks_per_h * info["stack"]
        rec = 0.0 if info["se"] else max(build_drain, default)
        rows.append({
            "item": name, "stack": info["stack"], "build_item": info["build"], "space_elevator": info["se"],
            "stock_now": now.get(name, 0), "built_total": round(snaps[-1].get("built", {}).get(name, 0)),
            "has_history": seen,
            "worst_drain_per_play_h": round(worst), "worst_build_drain_per_play_h": round(build_drain),
            "per_session": per,
            "per_session_net_per_h": [round(r["stock_change"] / r["play_h"]) for r in per],
            "default_per_play_h": default,
            "basis": "space elevator part: size to phase" if info["se"] else ("measured" if build_drain > default else "stack default"),
            "surplus_per_min": round(rec / 60, 2),
            "hours_of_cover": round(now.get(name, 0) / build_drain, 1) if build_drain else None,
        })
    play = [(b["play_s"] - a["play_s"]) / 3600 for a, b in sess]
    return {
        "world": world, "snapshots": len(snaps), "play_h_total": round(snaps[-1]["play_s"] / 3600, 2),
        "sessions_rated": len(sess), "session_play_h": [round(p, 2) for p in play],
        "stacks_per_play_h_default": stacks_per_h, "items": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--world", help="world (session) name; default: the newest save's")
    ap.add_argument("--item", action="append", default=[], help="limit to these items (repeatable)")
    ap.add_argument("--stacks-per-hour", type=float, default=1.0, help="default surplus with no history")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sessions", action="store_true", help="per-session storage change, built, factory net")
    a = ap.parse_args()
    if a.sessions and a.json:
        ap.error("--sessions is a text view; --json already carries per_session")
    r = report(a.world or newest_world(), a.item, a.stacks_per_hour)
    if a.json:
        print(json.dumps(r, indent=1))
        return
    print(f"{r['world']}: {r['snapshots']} snapshots, {r['play_h_total']} play h, "
          f"{r['sessions_rated']} sessions rated (play h each: {r['session_play_h']})")
    shown = [x for x in r["items"] if a.item or (x["build_item"] and (x["stock_now"] or x["worst_drain_per_play_h"]))]
    if a.sessions:
        for i, h in enumerate(r["session_play_h"]):
            print(f"\nsession {i + 1} ({h} play h)\n{'item':28} {'stock change':>12} {'built':>8} {'factory net':>11}")
            rows_i = [(x["item"], x["per_session"][i]) for x in shown]
            for name, p in sorted(rows_i, key=lambda t: t[1]["stock_change"]):
                if p["stock_change"] or p["built"]:
                    print(f"{name[:28]:28} {p['stock_change']:>12} {p['built']:>8} {p['factory_net']:>11}")
        return
    print(f"{'item':28} {'stack':>5} {'stock':>7} {'worst/h':>8} {'built/h':>8} {'cover h':>7} {'surplus/min':>11}  basis")
    for x in sorted(shown, key=lambda x: -x["surplus_per_min"] * 60 / max(x["stack"], 1)):
        cover = "-" if x["hours_of_cover"] is None else x["hours_of_cover"]
        print(f"{x['item'][:28]:28} {x['stack']:>5} {x['stock_now']:>7.0f} {x['worst_drain_per_play_h']:>8} {x['worst_build_drain_per_play_h']:>8} "
              f"{cover:>7} {x['surplus_per_min']:>11}  {x['basis']}")


if __name__ == "__main__":
    main()
