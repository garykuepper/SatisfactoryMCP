"""Which save this is, and where its players are standing."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

__all__ = ["SaveIdentity"]


@dataclass
class SaveIdentity:
    """The save's header, the key everything else hangs off, and the pawns in it."""

    projection: dict

    @property
    def header(self) -> dict:
        return self.projection.get("header", {})

    @property
    def world_id(self) -> str:
        """Stable per-world key. Labels hang off this, so it must survive autosave
        rotation and renaming -- which saveIdentifier does and the filename does not."""
        h = self.header
        return h.get("save_identifier") or f"session:{h.get('session_name') or '?'}"

    @property
    def age_note(self) -> str:
        """Human-readable provenance. Always shown: autosaves rotate every ~5 min
        and can catch the factory mid-restructure."""
        h = self.header
        kind = "autosave" if "autosave" in h.get("filename", "").lower() else "manual save"
        hours = (h.get("play_duration_s") or 0) / 3600
        return (
            f"{h.get('filename', '?')} ({kind}, world {h.get('session_name', '?')!r}, "
            f"{hours:.0f}h played, saveVersion {h.get('save_version')})"
        )

    @cached_property
    def players(self) -> list[dict]:
        """Player pawns with positions.

        Read from Char_Player_C, never BP_PlayerState_C: the state actor sits at the
        world origin, so using it would place every player at (0, 0).
        """
        return [p for p in self.projection.get("players", ()) if p.get("pos")]

    def player_position(self) -> tuple[float, float, float] | None:
        """Where the player is, in centimetres. None if the save has no pawn.

        With several pawns (co-op, or a stale disconnected one) the one holding a
        build gun wins, since that is the one actually being played.
        """
        if not self.players:
            return None
        armed = [p for p in self.players if p.get("has_build_gun")]
        pick = (armed or self.players)[0]
        x, y, z = pick["pos"]
        return (float(x), float(y), float(z))
