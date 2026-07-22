from __future__ import annotations

from collections.abc import Callable

ProgressCallback = Callable[[int], None]
CancelCallback = Callable[[], bool]


class GenerationCancelled(RuntimeError):
    """Raised when a map generation or export cancellation callback returns true."""


def check_cancelled(cancel_fn: CancelCallback | None) -> None:
    if cancel_fn is not None and cancel_fn():
        raise GenerationCancelled("Map operation cancelled")


class Progress:
    """Small monotonic percentage adapter shared by generation and export."""

    def __init__(self, total: int, callback: ProgressCallback | None) -> None:
        self.total = max(1, total)
        self.callback = callback
        self.done = 0
        self.last = -1

    def advance(self, count: int = 1) -> None:
        self.done = min(self.total, self.done + count)
        self.report(round(self.done * 100 / self.total))

    def report(self, percent: int) -> None:
        percent = max(self.last, min(100, percent))
        if percent == self.last:
            return
        self.last = percent
        if self.callback is not None:
            self.callback(percent)

    def finish(self) -> None:
        self.report(100)
