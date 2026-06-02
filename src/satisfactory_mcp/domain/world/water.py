"""Which bodies of water are already being drawn from, and where sea level is."""

from __future__ import annotations

__all__ = ["water_volumes"]


def water_volumes(projection: dict) -> dict:
    """Water Extractors grouped by the body of water they draw from, plus sea level.

    OQ5 said water pumps "carry no node, purity or geometry", and concluded they could
    not be matched to anything. Two thirds of that is right and the conclusion was not:
    `mExtractableResource` points at a named `FGWaterVolume`, the sidecar has been
    storing it in ``node`` the whole time, and it groups this save's 23 pumps into
    three distinct bodies (13 / 6 / 4). The volume OBJECT is level geometry and is not
    in the save, so its shape and capacity really are unknowable -- but its identity
    is not, and identity is enough to say how many separate shorelines are already in
    use.

    Sea level falls out of the same rows. Every pump on this save sits at -17.3 or
    -17.5 m, which turns "water must be drawn at sea level" from a rule of thumb into
    a measured number that deck ordering can be checked against.
    """
    groups: dict[str, list[dict]] = {}
    zs: list[float] = []
    for e in projection.get("extractors", ()):
        if e["cls"] != "Build_WaterPump_C":
            continue
        groups.setdefault(e.get("node") or "(unresolved)", []).append(e)
        if e.get("pos"):
            zs.append(e["pos"][2] / 100.0)
    return {
        "volumes": {
            k.rsplit(".", 1)[-1]: len(v)
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        },
        "pumps": sum(len(v) for v in groups.values()),
        "sea_level_m": (sum(zs) / len(zs)) if zs else None,
        "sea_level_span_m": (max(zs) - min(zs)) if zs else None,
    }
