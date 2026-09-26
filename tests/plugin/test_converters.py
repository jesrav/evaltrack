"""A converter registered in a `conftest.py` decides what a real pytest session
saves for the values of its type."""

from pathlib import Path

import pytest

from evaltrack.core import converters as registry

from .helpers import make_eval_source, read_run, run_pytest

pytest_plugins = ["pytester"]

_CONFTEST = """\
import evaltrack.converters

evaltrack.converters.register(
    "heavy",
    native_types=("test_x.Heavy",),
    convert=lambda value: {"answer": value.answer},
)
"""

_HEAVY = """\

class Heavy:
    def __init__(self, answer):
        self.answer = answer
        self.state = "x" * 1000
"""


@pytest.fixture(autouse=True)
def _own_registry(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested session runs in this process, so its registration would
    outlive the test without a copy of the registry."""
    monkeypatch.setattr(registry, "_registry", dict(registry._registry))  # pyright: ignore[reportPrivateUsage]


def test_a_converter_from_conftest_decides_what_the_run_saves(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(
        test_x=make_eval_source(preamble=_HEAVY, task="return Heavy(x)")
    )
    run_file = tmp_path / "run.json"
    result = run_pytest(pytester, f"--evaltrack-run-file={run_file}")
    result.assert_outcomes(passed=1)
    case = read_run(run_file)["tests"]["test_x.py::test_eval"]["cases"]["c"]
    assert case["attempts"][0]["output"] == {"answer": "x"}
