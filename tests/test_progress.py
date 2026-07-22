import pytest

from hoi4.progress import OperationCancelled, check_cancelled, report_progress


def test_progress_event_has_clamped_fraction() -> None:
    events = []
    report_progress(
        events.append,
        operation="validate",
        phase="done",
        current=12,
        total=10,
        message="complete",
    )

    assert events[0].fraction == 1.0
    assert events[0].message == "complete"


def test_cancel_callback_raises_typed_exception() -> None:
    with pytest.raises(OperationCancelled, match="validation cancelled"):
        check_cancelled(lambda: True, operation="validation")
