"""Notice that a save -- or a note written about one -- changed, and tell every browser.

Two trees, because two things move under a player who is using both halves at once: the game
writes ``.sav`` files, and this project writes factory labels and stored plans beside them.
They are published as two SSE event names so the page can refetch the half that moved.

Polling, not a filesystem watch: Satisfactory rewrites an autosave in place and the write is
not atomic, so an inotify-style event fires mid-write and a reader gets a torn file, whereas
a poll of the modification times can only observe the mtime the finished write stamped.
Fan-out is one ``asyncio.Queue`` per subscriber, because a shared queue means the first
browser to read an event is the only one that sees it. The queues are bounded and drop
rather than block: a browser that has stopped reading has gone away, and these are edge
triggers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ... import config

__all__ = ["KINDS", "KIND_NOTES", "KIND_SAVE", "POLL_SECONDS", "SaveWatcher", "WatchEvent"]

log = logging.getLogger(__name__)

#: Slow enough to be free, fast enough that a manual save shows up before the player
#: has alt-tabbed back to the browser.
POLL_SECONDS = 3.0

#: Per-subscriber backlog. Deep enough not to matter: two pending triggers ask the page for
#: the same refetch twice.
QUEUE_MAX = 4

#: The game wrote a save.
KIND_SAVE = "save"

#: This project wrote a note about one -- a factory label, or a stored plan. The one
#: collaborative moment (name it, then look at the map) writes here and never to a ``.sav``.
KIND_NOTES = "notes"

#: Every kind, in the order a newly connected browser is told about them.
KINDS = (KIND_SAVE, KIND_NOTES)


@dataclass(frozen=True)
class WatchEvent:
    """The newest file in one watched tree, at the moment its mtime changed.

    ``kind`` is the SSE event NAME and is deliberately not in ``as_dict``: the wire carries
    it as ``event:``, and a copy in the data would be two places to read one fact.
    """

    kind: str
    filename: str
    mtime: float

    def as_dict(self) -> dict:
        return {"filename": self.filename, "mtime": self.mtime}


class SaveWatcher:
    """Polls the watched trees and fans changes out to per-subscriber queues."""

    def __init__(
        self,
        root: Path | None = None,
        notes: Iterable[Path] | None = None,
        interval: float = POLL_SECONDS,
    ) -> None:
        #: ``None`` means "ask config every scan", so a test that repoints
        #: ``config.saves_root`` is obeyed without rebuilding the watcher.
        self._root = root
        #: The same, for the label and plan directories.
        self._notes = None if notes is None else tuple(notes)
        self.interval = interval
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        #: The newest event of each kind, replayed to every new subscriber.
        self.latest: dict[str, WatchEvent] = {}
        #: Polls that raised in a row, zeroed by any poll that gets through: "the watcher is
        #: broken now", never "the watcher hiccuped once in March".
        self.consecutive_failures = 0

    # ---- subscription ---------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, event: WatchEvent) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---- the poll -------------------------------------------------------

    def root(self) -> Path:
        return self._root if self._root is not None else config.saves_root()

    def notes_roots(self) -> tuple[Path, ...]:
        """Where this project writes about a save: factory labels, and stored plans."""
        if self._notes is not None:
            return self._notes
        return (config.labels_dir(), config.plans_dir())

    def _newest(self, kind: str, roots: Iterable[Path], pattern: str) -> WatchEvent | None:
        newest: tuple[float, str] | None = None
        for root in roots:
            try:
                for path in root.rglob(pattern):
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, path.name)
            except OSError:
                continue
        if newest is None:
            return None
        return WatchEvent(kind=kind, filename=newest[1], mtime=newest[0])

    def scan(self) -> list[WatchEvent]:
        """The newest file in each watched tree, for the trees that have one.

        Blocking, so it is called through ``asyncio.to_thread``: a disk walk on the event
        loop stalls the requests it is serving.

        Newest-mtime, which is what makes a rewritten label a change. A DELETED note is
        noticed only when it was the newest file in its tree.
        """
        found = [
            self._newest(KIND_SAVE, (self.root(),), "*.sav"),
            self._newest(KIND_NOTES, self.notes_roots(), "*.json"),
        ]
        return [event for event in found if event is not None]

    async def poll_once(self) -> list[WatchEvent]:
        """One scan; publishes and returns the events whose tree actually moved."""
        news: list[WatchEvent] = []
        for event in await asyncio.to_thread(self.scan):
            if event == self.latest.get(event.kind):
                continue
            self.latest[event.kind] = event
            self._publish(event)
            news.append(event)
        return news

    async def _run(self) -> None:
        # The first scan establishes the baseline and publishes, which is what gives a
        # browser that connected before the first poll something to draw.
        #
        # The catch-all must stay: a save directory that vanished mid-poll is no reason to
        # stop watching. It must also stay LOUD -- ``poll_once`` fans out as well as scans,
        # and a bug in the fan-out raises on every poll, which without a log is a server
        # that spins for its whole life while every browser sits on a page that never
        # updates again. Throttled to the first failure and every twentieth after it,
        # because a line every 3 s is a log nobody can read.
        while True:
            try:
                await self.poll_once()
                self.consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.consecutive_failures += 1
                if self.consecutive_failures == 1 or self.consecutive_failures % 20 == 0:
                    log.warning(
                        "save watcher poll failed (%d in a row); still polling every %.0fs",
                        self.consecutive_failures,
                        self.interval,
                        exc_info=True,
                    )
            await asyncio.sleep(self.interval)

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="save-watcher")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
