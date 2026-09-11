"""Git context detection, deferred until a marked test actually records."""

from pathlib import Path

import pytest

from evaltrack.pytest_plugin import (
    _dirty_check_key,  # pyright: ignore[reportPrivateUsage]
    _git_runner_key,  # pyright: ignore[reportPrivateUsage]
)

from .helpers import (
    EVAL_TEST_SOURCE,
    nested_run_args,
    read_run,
)

pytest_plugins = ["pytester"]


# --- git context detection (deferred until a run is recorded) ---


class _GitSpy:
    """Fake git runner and dirty check that record every call.

    They spawn no `git` subprocess. For the contracts, see
    `evaltrack.context.build_from_git`.
    """

    def __init__(
        self, outputs: dict[str, str] | None = None, dirty: bool = False
    ) -> None:
        self.outputs = outputs or {}
        self.dirty = dirty
        self.runner_calls: list[list[str]] = []
        self.dirty_check_calls = 0

    def runner(self, args: list[str]) -> str | None:
        self.runner_calls.append(args)
        return self.outputs.get(" ".join(args))

    def dirty_check(self) -> bool | None:
        self.dirty_check_calls += 1
        return self.dirty

    @property
    def was_called(self) -> bool:
        return bool(self.runner_calls) or self.dirty_check_calls > 0


class _InjectGitSpy:
    """Plugin for the inner session that puts the spy's callables on the config.

    `tryfirst` stores them before evaltrack's own `pytest_configure` runs, so a
    detection that fired that early would reach the fakes rather than real git.
    That early detection is the regression this test guards against.
    """

    def __init__(self, spy: _GitSpy) -> None:
        self._spy = spy

    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(self, config: pytest.Config) -> None:
        config.stash[_git_runner_key] = self._spy.runner
        config.stash[_dirty_check_key] = self._spy.dirty_check


def test_no_git_detection_when_no_marked_tests(pytester: pytest.Pytester) -> None:
    """A suite that never uses the `evaltrack` marker must not pay for git
    context detection because the plugin is installed. A plain pytest run makes
    no git call at all."""
    spy = _GitSpy()
    pytester.makepyfile(test_x="def test_plain():\n    assert True\n")
    # Explicitly in-process so the spy's closures can be injected.
    result = pytester.runpytest_inprocess(
        *nested_run_args(), plugins=[_InjectGitSpy(spy)]
    )
    result.assert_outcomes(passed=1)
    assert not spy.was_called, "an unmarked suite must not touch git"


def test_recorded_context_is_detected_when_marked_tests_ran(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection at session end must not lose the context. When a marked test
    records a report, the saved run still carries the detected git context:
    commit, dirty flag and branch label."""
    for var in ("EVALTRACK_COMMIT", "EVALTRACK_BRANCH"):
        monkeypatch.delenv(var, raising=False)
    spy = _GitSpy(
        outputs={
            "rev-parse HEAD": "abc123",
            "rev-parse --abbrev-ref HEAD": "feature/x",
        },
        dirty=True,
    )
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = pytester.runpytest_inprocess(
        *nested_run_args(f"--evaltrack-run-file={out}"),
        plugins=[_InjectGitSpy(spy)],
    )
    result.assert_outcomes(passed=1)
    assert spy.was_called
    data = read_run(out)
    assert data["commit"] == "abc123"
    assert data["worktree_dirty"] is True
    assert data["labels"]["branch"] == "feature/x"
