"""Shared setup for the `evaltrack.marked_test` tests.

Every test here needs a `MarkedTest` bound as the active one, because that is
what `evaltrack.run` reads. The binding is thread-local and process-wide, so it
has to be restored afterwards or the next test records into this one's
recorder.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

import pytest

from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_record import MarkerSettings
from evaltrack.marked_test import MarkedTest, bind_marked_test

TEST_NODEID = "tests/test_x.py::test_eval"

# What the `bind` fixture hands a test: call it with marker settings, get the
# bound `MarkedTest` back.
Bind = Callable[..., MarkedTest]


def make_marked_test(**settings: Any) -> MarkedTest:
    """A `MarkedTest` with the marker settings given."""
    return MarkedTest(
        TEST_NODEID,
        recorder=EvalRecorder(),
        settings=MarkerSettings(**settings),
    )


@pytest.fixture
def bind() -> Iterator[Bind]:
    """Bind a `MarkedTest` as the active one for the rest of the test.

    Call it with the marker settings the test needs; it returns what it bound.
    The unbinding is the fixture's, so a test that raises still leaves the
    binding as it found it.
    """
    with ExitStack() as bindings:

        def _bind(**settings: Any) -> MarkedTest:
            active = make_marked_test(**settings)
            bindings.enter_context(bind_marked_test(active))
            return active

        yield _bind
