"""At the end of the session, evaltrack reports each attempt with a large stored
output, and names its test and case."""

from pathlib import Path

import pytest

from evaltrack.core.recorder import LargeOutput
from evaltrack.pytest_plugin import describe_large_outputs

from .helpers import make_eval_source, run_pytest

pytest_plugins = ["pytester"]

# Two cases, one of them answering with more than the default limit.
_LARGE_OUTPUT_BODY = """\
dataset = Dataset(
    name="d",
    cases=[Case(name="short", inputs="x"), Case(name="long", inputs="y")],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
"""


def test_a_large_output_is_reported_with_its_test_and_case(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    pytester.makepyfile(
        test_x=make_eval_source(
            task="return x * 300_000 if x == 'y' else x", body=_LARGE_OUTPUT_BODY
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={tmp_path / 'run.json'}")
    result.assert_outcomes(passed=1)
    out = result.stdout.str()
    assert "1 attempt(s) recorded an output larger than 256 KB" in out
    assert "test_x.py::test_eval, case 'long'" in out
    assert "docs/outputs.md#large-outputs" in out


def test_an_ordinary_run_reports_no_large_output(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    pytester.makepyfile(test_x=make_eval_source())
    result = run_pytest(pytester, f"--evaltrack-run-file={tmp_path / 'run.json'}")
    result.assert_outcomes(passed=1)
    assert "recorded an output larger than" not in result.stdout.str()


def test_a_test_with_many_large_outputs_gets_one_line_naming_the_largest() -> None:
    lines = describe_large_outputs(
        [
            LargeOutput("t.py::test_a", "c1", 300 * 1024),
            LargeOutput("t.py::test_a", "c2", 2 * 1024 * 1024),
            LargeOutput("t.py::test_b", "c1", 400 * 1024),
        ],
        limit=256 * 1024,
    )
    assert lines[0] == "3 attempt(s) recorded an output larger than 256 KB:"
    assert lines[1] == "    t.py::test_a, case 'c2': 2.0 MB (and 1 more in this test)"
    assert lines[2] == "    t.py::test_b, case 'c1': 400 KB"
