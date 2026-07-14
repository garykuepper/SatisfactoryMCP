"""Fanning a whole-folder test out over the machine, without fighting the test runner for it.

Three tests here walk the reader's entire save directory: the vendored-parser parity replay
(31 sidecar subprocesses), the Hermite arc-length measurement and the lightweight-record
split. Each was a `for path in sorted(root.rglob("*.sav"))` loop doing one save at a time,
and between them they were 146 s of a 198 s integration run -- 74% of the wall clock in
three tests out of 805.

They parallelise well because a save is independent of every other save. What needs saying
out loud is the two things that are easy to get wrong when they do.

**One: the suite is already parallel around them.** ``addopts`` carries ``-n 8``, so these
three run on three ``pytest-xdist`` workers while the other 802 tests churn through the rest,
and a test that then opens children of its own is bidding against its own siblings. The
tempting conclusion -- be modest, take a third of the machine each -- is measurably wrong,
and ``fanout_width`` carries the table that says so.

**Two: parallel must not mean unordered.** Every one of these tests reports per save --
"``Han solo`` drifted on ``inventories``", "more than the known non-save was refused" -- and
one of them stops early once it has enough evidence. A pool that returned results as they
finished would make the *set* of saves measured depend on which core won, which turns a
falsifiable measurement into a flaky one. ``in_order`` fans out but hands results back in
the order they were submitted, so what the assertions see is what the serial loop saw.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Executor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Read by ``fanout_width``, and the only reason it exists is that the choice below was
#: measured rather than reasoned to. Set it to compare widths without editing three files.
WIDTH_ENV = "SATISFACTORY_TEST_FANOUT"


def fanout_width() -> int:
    """How many children one whole-folder test should run at once. Half the logical CPUs.

    **Half, because that is where it measured best, and the first guess was wrong in an
    instructive way.** The instinct was that these tests should be modest: the suite is
    already running eight ``xdist`` workers, three whole-folder tests can be in flight
    together, so give each a third of the machine and do not fight the runner. That is what
    this function did, and it left most of the win on the table. Whole integration set on
    this 16-core / 32-thread machine, everything else held equal:

    ====== ========
    width  wall
    ====== ========
    4      43.9 s
    8      32.5 s
    16     22.6 s
    24     25.0 s
    32     24.0 s
    ====== ========

    The reasoning the numbers support: these three ARE the tail, so time spent being polite
    to the other 802 tests is time the run cannot get back -- the cheap tests fill the gaps
    around a saturated machine perfectly well, and the whole-folder tests do not. The turn at
    24 is the other half of it: this is CPU-bound parsing, so the second thread of each core
    contends rather than helps, and half the logical count lands on the physical one.

    Deliberately NOT conditioned on ``PYTEST_XDIST_WORKER``. It was, and the table above is
    what happened: the runner's own width and this one do not need to be reconciled, because
    oversubscription costs far less here than an idle core on the critical path.

    Never below 2: a "parallel" path that degenerates to one child on a small machine is a
    second code path that nothing ever exercises, and the whole point is that there is one.
    """
    override = os.environ.get(WIDTH_ENV)
    if override:
        return max(1, int(override))
    return max(2, (os.cpu_count() or 4) // 2)


def in_order(
    executor: Executor,
    items: Iterable[T],
    work: Callable[[T], R],
    *,
    width: int,
) -> Iterator[tuple[T, R]]:
    """Yield ``(item, work(item))`` in the order of ``items``, ``width`` calls in flight.

    A bounded look-ahead rather than ``Executor.map``: ``map`` submits the whole iterable at
    once, so a caller that stops early has already paid for every save on the disk. Here at
    most ``width`` are ever outstanding, and abandoning the generator cancels what has not
    started -- which is what lets the arc-length test keep its "enough evidence" break and
    still get the speed-up.

    Results come back in submission order, so a caller's accumulators, its early stop and
    its per-item failure messages are all bit-identical to the serial loop's. The cost is
    that one slow item stalls the ones behind it; on a save folder, where the work per file
    is within a factor of three, that is worth an order that assertions can rely on.
    """
    pending: deque = deque()
    source = iter(items)
    try:
        for item in source:
            pending.append((item, executor.submit(work, item)))
            if len(pending) < width:
                continue
            done, future = pending.popleft()
            yield done, future.result()
        while pending:
            done, future = pending.popleft()
            yield done, future.result()
    finally:
        # The caller broke out, or something raised. Nothing started can be recalled, but
        # everything merely queued can, and on a folder of 31 saves that is most of it.
        for _item, future in pending:
            future.cancel()
