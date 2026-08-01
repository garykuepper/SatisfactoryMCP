"""Which save this is, and where its players are standing."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ...core.text import ago, stamp

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
        and can catch the factory mid-restructure.

        The mtime rides in every response since a client was twice told "nothing is
        here" by an autosave hours behind the live session -- the file's age is the
        one number that would have said so, and it was on disk the whole time. The
        autosave clause is a WARNING, not decoration: a manual save is a moment the
        player chose, an autosave is whenever the timer last fired, so only the
        latter earns "disk may lag the world".
        """
        h = self.header
        is_autosave = "autosave" in h.get("filename", "").lower()
        kind = "autosave" if is_autosave else "manual save"
        hours = (h.get("play_duration_s") or 0) / 3600
        written = stamp(h.get("mtime_ns"))
        when = f", written {written} ({ago(h.get('mtime_ns'))})" if written else ""
        note = (
            f"{h.get('filename', '?')} ({kind}, world {h.get('session_name', '?')!r}, "
            f"{hours:.0f}h played, saveVersion {h.get('save_version')}{when})"
        )
        if is_autosave:
            note += (
                " -- the game writes autosaves periodically, so disk may lag the live world"
            )
        return note

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
