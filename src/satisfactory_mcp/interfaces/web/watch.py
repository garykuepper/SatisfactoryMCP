"""Notice that the game wrote a save, and tell every connected browser.

Polling, not a filesystem watch. Satisfactory rewrites an autosave in place every few
minutes and the write is not atomic, so an inotify-style event fires mid-write and a
reader gets a torn file. A 3 s poll of the modification times costs one directory walk
and cannot observe anything but the mtime the write already finished stamping.

Fan-out is one ``asyncio.Queue`` per subscriber rather than a shared one, because a
shared queue means the first browser to read an event is the only one that sees it. The
queues are bounded and drop rather than block: a browser that has stopped reading is a
browser that has gone away, and the newest save is the only one worth telling it about.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from ... import config

__all__ = ["POLL_SECONDS", "SaveEvent", "SaveWatcher"]

log = logging.getLogger(__name__)

#: Slow enough to be free, fast enough that a manual save shows up before the player
#: has alt-tabbed back to the browser.
POLL_SECONDS = 3.0

#: Per-subscriber backlog. One is enough: the events are edge triggers telling the page
#: to refetch, and two pending triggers ask for the same refetch twice.
QUEUE_MAX = 4


@dataclass(frozen=True)
class SaveEvent:
    """The newest save on disk, at the moment its mtime changed."""

    filename: str
    mtime: float

    def as_dict(self) -> dict:
        return {"filename": self.filename, "mtime": self.mtime}


class SaveWatcher:
    """Polls the save tree and fans changes out to per-subscriber queues."""

    def __init__(self, root: Path | None = None, interval: float = POLL_SECONDS) -> None:
        #: ``None`` means "ask config every scan", so a test that repoints
        #: ``config.saves_root`` is obeyed without rebuilding the watcher.
        self._root = root
        self.interval = interval
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self.latest: SaveEvent | None = None
        #: Polls that raised in a row. Zeroed by any poll that gets through, so this is
        #: "the watcher is broken now" and never "the watcher hiccuped once in March".
        self.consecutive_failures = 0

    # ---- subscription ---------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, event: SaveEvent) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # See the module docstring: a full queue is a reader that stopped
                # reading, and dropping is the correct answer for an edge trigger.
                pass

    # ---- the poll -------------------------------------------------------

    def root(self) -> Path:
        return self._root if self._root is not None else config.saves_root()

    def scan(self) -> SaveEvent | None:
        """The newest ``.sav`` under the save root, or ``None`` when there is none.

        Blocking, and called through ``asyncio.to_thread``: the reference save tree is
        a few hundred files across several account folders, which is nothing, but it is
        still a disk walk and the event loop is also serving requests.
        """
        try:
            newest: tuple[float, str] | None = None
            for path in self.root().rglob("*.sav"):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, path.name)
        except OSError:
            return None
        if newest is None:
            return None
        return SaveEvent(filename=newest[1], mtime=newest[0])

    async def poll_once(self) -> SaveEvent | None:
        """One scan; publishes and returns the event only when the mtime moved."""
        event = await asyncio.to_thread(self.scan)
        if event is None or event == self.latest:
            return None
        self.latest = event
        self._publish(event)
        return event

    async def _run(self) -> None:
        # The first scan establishes the baseline. It publishes too, which is what
        # gives a browser that connected before the first poll something to draw.
        #
        # The catch-all stays -- a save directory that vanished mid-poll is not a reason
        # to stop watching, and the next scan will find it or keep finding nothing -- but
        # it no longer swallows the OTHER thing that can raise here. `poll_once` also
        # calls `_publish`, and a bug in the fan-out raises on every single poll: this
        # loop then span for the lifetime of the server, three seconds at a time, telling
        # nobody, while every connected browser sat on a page that never updated again.
        # Silence and "no save has changed" looked exactly alike.
        #
        # So: the first failure is logged with its traceback, and every twentieth after
        # that. Logged once rather than each time because the failure mode this exists for
        # is the one that repeats forever, and a line every 3 s is a log nobody can read.
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
