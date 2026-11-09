"""Transport recovery cannot silently broaden an inference retry budget."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_context_recover_v12 import RETRYABLE_STOP, supervise


def test_balance_outage_resumes_without_changing_observer():
    calls, events, sleeps = [], [], []

    def observer():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError(RETRYABLE_STOP)

    supervise(observer, lambda *args: events.append(args),
              sleep=sleeps.append, clock=lambda: 0)
    assert len(calls) == 3
    assert sleeps == [30, 30]
    assert events[-1] == ("observer_returned", 2)


@pytest.mark.parametrize("message", [
    "Interrupted native API attempt needs uniform recovery review",
    "DeepSeek authorization/account failure; queue stopped",
    "Predeclared account limit reached",
    "Frozen native observation protocol changed",
])
def test_nontransport_failure_is_not_retried(message):
    calls = []

    def observer():
        calls.append(1)
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match=message):
        supervise(observer, lambda *args: None,
                  sleep=lambda _: pytest.fail("Unexpected retry"))
    assert len(calls) == 1


def test_recovery_wait_limit_stops():
    times = iter([0, 31])
    events = []

    def observer():
        raise RuntimeError(RETRYABLE_STOP)

    with pytest.raises(RuntimeError, match=RETRYABLE_STOP):
        supervise(observer, lambda *args: events.append(args),
                  sleep=lambda _: pytest.fail("Retry after limit"),
                  clock=lambda: next(times), maximum_wait=30)
    assert events == [("recovery_wait_limit_reached", 1)]
