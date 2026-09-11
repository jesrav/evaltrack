"""Shared code for the plugin tests.

Every test here runs a real pytest session with `pytester`, then reads back
what evaltrack recorded. The source builders and the run readers are shared
rather than rebuilt in each module.
"""

import json
import textwrap
from pathlib import Path
from typing import Any

# Imported for its side effect on this process, not for its API. Every session
# here runs pytest inside pytest, and `pytester` snapshots `sys.modules` around
# each inner run. A module the inner test imports is dropped afterwards, then
# imported again by the next one. This import in the outer process keeps
# pydantic-evals inside that snapshot, so it loads once instead of once per
# test.
import pydantic_evals  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest


def nested_run_args(*args: str) -> tuple[str, ...]:
    """The plugin arguments for a pytest session inside `pytester`.

    The cache plugin is off. Every session here wants that, and none of them
    tests it.

    pytest-rerunfailures and pytest-xdist are installed for the few tests that
    need them. With both active, rerunfailures opens a socket per session and
    never closes it. Python collects it later, during some unrelated test, and
    that test fails. No session here needs both plugins, so this drops the one
    the caller did not ask for.

    pytest-asyncio comes along with deepeval and warns at configure time unless
    the session sets its fixture loop scope. An inner session gets a fresh
    config file, so it cannot inherit that setting, and the warning aborts it.
    It stays off for every session that did not ask for it.
    """
    keep_reruns = any(a.startswith("--reruns") for a in args)
    unused = ["xdist" if keep_reruns else "rerunfailures"]
    if not any("asyncio_" in arg for arg in args):
        unused.append("asyncio")
    off = [flag for name in unused for flag in ("-p", f"no:{name}")]
    return (*args, "-p", "no:cacheprovider", *off)


def run_pytest(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """`pytester.runpytest` under `nested_run_args`."""
    return pytester.runpytest(*nested_run_args(*args))


def indent_block(code: str, level: int) -> str:
    return textwrap.indent(code.strip("\n"), " " * level)


# The dataset a marked test evaluates when it has no reason to build its own.
# One named case and one evaluator, handed to evaltrack so it can run the eval
# again.
DEFAULT_BODY = """\
dataset = Dataset(
    name="d",
    cases=[Case(name="c", inputs="x", expected_output="x")],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
"""


def make_eval_source(
    *,
    name: str = "test_eval",
    marker: str = "@pytest.mark.evaltrack",
    evaluator: str = "return True",
    task: str = "return x",
    args: str = "",
    preamble: str = "",
    body: str = DEFAULT_BODY,
    async_def: bool = False,
) -> str:
    """Source for a pytest module holding one evaltrack-marked test.

    Every session these tests run shares one shape, so a caller passes in only
    the parts it varies. `evaluator` and `task` are the bodies of
    `_Evaluator.evaluate` and of the async `_task`. `preamble` is module-level
    code, such as a shared counter or an extra class. `body` is the test's own
    body. `async_def` makes the test a coroutine, for a session that loads an
    async plugin to run it.
    """
    return f"""\
import asyncio
import pathlib
import pytest
import evaltrack
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator
{preamble}

class _Evaluator(Evaluator):
    def evaluate(self, ctx):
{indent_block(evaluator, 8)}

async def _task(x):
{indent_block(task, 4)}

{marker}
{"async " if async_def else ""}def {name}({args}):
{indent_block(body, 4)}
"""


EVAL_TEST_SOURCE = make_eval_source(name="test_records_eval")

# The SENTINEL file exists only if the test body ran, so a test of a
# configure-time abort can assert that no test ran. It proves the run, not only
# the parse of an outcome.
SENTINEL_TEST_SOURCE = (
    EVAL_TEST_SOURCE
    + """\

@pytest.mark.evaltrack
def test_writes_sentinel():
    pathlib.Path('SENTINEL').write_text('ran')
"""
)


def read_run(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_stored_attempts(case: dict[str, Any]) -> list[dict[str, Any]]:
    """A stored case's attempts, earliest first."""
    return list(case["attempts"])


def find_nodeid(data: dict[str, Any], name: str) -> str:
    """Find the run's nodeid for a test function name.

    The recorder keys tests by their pytest nodeid (for example
    ``test_x.py::test_eval``), so the function name is the suffix after ``::``.
    """
    matches = [k for k in data["tests"] if k == name or k.endswith(f"::{name}")]
    assert len(matches) == 1, f"Expected one test matching {name!r}, got {matches}"
    return matches[0]


class RecordedRun:
    """Reader for the run a session wrote, keyed by test function name.

    The file is read on first use, because a session that aborts or records
    nothing never writes one.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = read_run(self.path)
        return self._data

    def test(self, name: str) -> dict[str, Any]:
        return self.data["tests"][find_nodeid(self.data, name)]

    def case(self, name: str, case: str = "c") -> dict[str, Any]:
        return self.test(name)["cases"][case]


def run_eval_session(
    pytester: pytest.Pytester, tmp_path: Path, source: str, *args: str
) -> tuple[pytest.RunResult, RecordedRun]:
    """Run `source` as a one-module session that writes its run to `tmp_path`.

    The shape nearly every session here shares: one test module, the run
    written as JSON, and a reader for what came back.
    """
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=source)
    return run_pytest(pytester, f"--evaltrack-run-file={out}", *args), RecordedRun(out)


def assert_aborted_before_any_test(
    result: pytest.RunResult, pytester: pytest.Pytester
) -> None:
    """The session refused to start with a usage error, and no test ran.

    The session must use `SENTINEL_TEST_SOURCE`, whose file exists only once a
    test body ran. Without it this only pins the exit code, not the ordering.
    """
    assert result.ret == pytest.ExitCode.USAGE_ERROR, (
        "an unusable save target must be refused at startup as a usage error"
    )
    assert not (pytester.path / "SENTINEL").exists(), (
        "the abort must come before any test body runs"
    )


# Fails the first attempt and passes from the second. The first failure fires
# the marker's rerun loop before the test body reaches the second call.
FLAKY_ONCE_PREAMBLE = '_calls = {"n": 0}'
FLAKY_ONCE_EVALUATOR = """\
_calls["n"] += 1
return _calls["n"] >= 2
"""


def make_score_source(
    value: float,
    *,
    marker: str = "@pytest.mark.evaltrack(score_bars={'quality': 0.8})",
) -> str:
    """A test whose only evaluator emits a `quality` score of `value`."""
    return make_eval_source(
        name="test_scored",
        marker=marker,
        evaluator=f'return {{"quality": {value}}}',
    )
