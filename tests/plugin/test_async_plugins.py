"""Async tests must reach the gate, the same as sync tests.

evaltrack records and gates a marked `async def` test no matter which plugin
runs the coroutine. Each test here runs an inner pytest session whose eval has
one failing case. The gate must fail that inner test. If it passes instead,
the gate never ran.
"""

from pathlib import Path

import pytest

from .helpers import find_nodeid, make_eval_source, read_run, run_pytest

pytest_plugins = ["pytester"]


# A pinned backend keeps the test unparametrized, so its nodeid stays the plain
# function name used to find the recorded run.
_ANYIO_PREAMBLE = """
pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"
"""

# anyio drives every test coroutine from one runner task, and copies that
# task's context once, when it creates the task. A module- or session-scoped
# async fixture creates the task during its own setup, before any
# function-scoped fixture of the marked test runs.
_WIDER_ASYNC_FIXTURE = """

@pytest.fixture(scope="module")
async def warm():
    return "warm"
"""

# This test comes before the marked test, so it creates the runner task.
_UNRELATED_TEST_HOLDING_THE_FIXTURE = """

async def test_unrelated(warm):
    assert warm == "warm"
"""

_PYTEST_ASYNCIO_PREAMBLE = """
@pytest.fixture(scope="session")
async def warm():
    return "warm"
"""

_AWAIT_BODY = """\
dataset = Dataset(
    name="d",
    cases=[Case(name="c", inputs="x", expected_output="x")],
    evaluators=[_Evaluator()],
)
await evaltrack.run_async(dataset.evaluate, _task)
"""


def _make_async_source(*, preamble: str, args: str = "") -> str:
    return make_eval_source(
        name="test_async_eval",
        evaluator="return False",
        args=args,
        preamble=preamble,
        body=_AWAIT_BODY,
        async_def=True,
    )


def _assert_gated_and_recorded(
    result: pytest.RunResult, out: Path, *, other_passed: int = 0
) -> None:
    result.assert_outcomes(failed=1, passed=other_passed)
    result.stdout.fnmatch_lines(["*1 failing case(s): c*"])
    data = read_run(out)
    assert data["tests"][find_nodeid(data, "test_async_eval")]["outcome"] == "failed", (
        "the recorded run must agree the gate failed the test"
    )


def _run_anyio(pytester: pytest.Pytester, out: Path) -> pytest.RunResult:
    # pytest-asyncio is off, so the plugin installed next to evaltrack cannot
    # change what these sessions exercise.
    return run_pytest(pytester, f"--evaltrack-run-file={out}", "-p", "no:asyncio")


def test_anyio_gates_when_the_test_requests_a_wider_async_fixture(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """With a wider async fixture, anyio runs the test body in a context taken
    before evaltrack bound the marker to the test. The eval must still reach
    the gate. A failing eval that reports green is worse than one that never
    ran."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_async_source(
            preamble=_ANYIO_PREAMBLE + _WIDER_ASYNC_FIXTURE, args="warm"
        )
    )
    _assert_gated_and_recorded(_run_anyio(pytester, out), out)


def test_anyio_gates_when_an_unrelated_test_holds_the_wider_async_fixture(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """anyio shares one runner across the session. An unrelated test that holds
    the wider fixture is therefore enough to move the marked test into that
    context. The marked test asks for nothing async, so nothing in it warns the
    reader that its gate is at risk."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_async_source(
            preamble=_ANYIO_PREAMBLE
            + _WIDER_ASYNC_FIXTURE
            + _UNRELATED_TEST_HOLDING_THE_FIXTURE
        )
    )
    _assert_gated_and_recorded(_run_anyio(pytester, out), out, other_passed=1)


def test_anyio_gates_without_a_wider_async_fixture(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The control for the two tests before this one. Plain anyio creates the
    runner task during the setup of the marked test."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=_make_async_source(preamble=_ANYIO_PREAMBLE))
    _assert_gated_and_recorded(_run_anyio(pytester, out), out)


def test_pytest_asyncio_gates_with_a_wider_async_fixture(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The same shape under the other async plugin, which never lost the gate.
    This is a control, so a change to how evaltrack binds the marker cannot fix
    anyio by breaking pytest-asyncio.

    pytest-asyncio is not a dependency of this project, so this test skips when
    it is absent. CI installs it for this file, and `just asyncio_test` runs it
    the same way.
    """
    pytest.importorskip("pytest_asyncio")
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_async_source(preamble=_PYTEST_ASYNCIO_PREAMBLE, args="warm")
    )
    result = run_pytest(
        pytester,
        f"--evaltrack-run-file={out}",
        "-p",
        "no:anyio",
        "-o",
        "asyncio_mode=auto",
        # If this is unset, pytest-asyncio warns at configure time, and the
        # `filterwarnings = error` of this suite turns that warning into an
        # error inside the inner session.
        "-o",
        "asyncio_default_fixture_loop_scope=session",
    )
    _assert_gated_and_recorded(result, out)
