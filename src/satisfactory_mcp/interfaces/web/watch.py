"""Notice that the game wrote a save, and tell every connected browser.

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
from dataclasses import dataclass
from pathlib import Path

from ... import config

__all__ = ["POLL_SECONDS", "SaveEvent", "SaveWatcher"]

log = logging.getLogger(__name__)

#: Slow enough to be free, fast enough that a manual save shows up before the player
#: has alt-tabbed back to the browser.
POLL_SECONDS = 3.0

#: Per-subscriber backlog. Deep enough not to matter: two pending triggers ask the page for
#: the same refetch twice.
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

    def _publish(self, event: SaveEvent) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---- the poll -------------------------------------------------------

    def root(self) -> Path:
        return self._root if self._root is not None else config.saves_root()

    def scan(self) -> SaveEvent | None:
        """The newest ``.sav`` under the save root, or ``None`` when there is none.

        Blocking, so it is called through ``asyncio.to_thread``: a disk walk on the event
        loop stalls the requests it is serving.
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
