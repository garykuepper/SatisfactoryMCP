"""The one exception type the whole parser raises.

It lives in a module of its own so that ``reader``, the bottom layer, can raise it without
importing anything above it. That matters because the most common structural failure is the
dullest one -- a read that runs off the end of a torn file -- and it was previously a bare
``ValueError`` from ``Reader._take`` while the layers above raised something more specific.
Two types meant the sidecar, which catches exactly one at the save boundary, sent half of the
failures to its bare-``Exception`` handler and reported ``ValueError`` with no offset instead
of "could not read this save".

``ValueError`` is the base so that the earlier layers' callers, and their tests, keep
working: everything this replaces was already a ``ValueError``.
"""

from __future__ import annotations

__all__ = ["ParseError"]


class ParseError(ValueError):
    """A structural surprise, always with the byte offset that surprised us.

    The save under a running game is rewritten every few minutes, so reading a file
    mid-write is routine rather than exceptional. Every raise site names what was
    expected and where, because "the level list is 3 bytes short at 5798068" is a bug
    report and "list index out of range" is not.
    """
