"""Shared progress and cancellation primitives for long SDK operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from .errors import Hoi4Error


@dataclass(frozen=True)
class ProgressEvent:
    """One progress report of a long operation: which operation, which phase, how far."""

    operation: str
    phase: str
    current: int
    total: int
    message: str = ""

    @property
    def fraction(self) -> float:
        """How far the operation is, from 0.0 to 1.0 (1.0 when ``total`` is not positive)."""
        return 1.0 if self.total <= 0 else min(1.0, max(0.0, self.current / self.total))


ProgressCallback = Callable[[ProgressEvent], None]
CancelCallback = Callable[[], bool]


class OperationCancelled(Hoi4Error, RuntimeError):
    """Raised when a caller asks an interruptible SDK operation to stop."""


def report_progress(
    callback: ProgressCallback | None,
    *,
    operation: str,
    phase: str,
    current: int,
    total: int,
    message: str = "",
) -> None:
    if callback is not None:
        callback(
            ProgressEvent(
                operation=operation,
                phase=phase,
                current=current,
                total=total,
                message=message,
            )
        )


def check_cancelled(callback: CancelCallback | None, *, operation: str) -> None:
    if callback is not None and callback():
        raise OperationCancelled(f"{operation} cancelled")
