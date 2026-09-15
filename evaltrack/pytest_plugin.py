"""The pytest plugin. Records each test's eval and fails the test when a case fails.

A marked test that records no eval fails. At session end the run is saved to a
repository, or written as JSON with `--evaltrack-run-file`.
"""

import dataclasses
import inspect
import os
import sys
import uuid
from collections.abc import Callable, Generator
from difflib import get_close_matches
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

from evaltrack.config import load_config, resolve_local
from evaltrack.context import detect
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_context import RunContext
from evaltrack.core.run_record import MarkerSettings, dump_run_json
from evaltrack.marked_test import MarkedTest, bind_marked_test
from evaltrack.repositories import open_repository

# A test puts its own git runner and dirty check here, instead of running git.
_git_runner_key: pytest.StashKey[Callable[[list[str]], str | None]] = pytest.StashKey()
_dirty_check_key: pytest.StashKey[Callable[[], bool | None]] = pytest.StashKey()


def _in_xdist_worker(config: pytest.Config) -> bool:
    """`workerinput` is the attribute xdist sets on a worker's config. The
    `PYTEST_XDIST_WORKER` variable is not used, since a nested session started from
    a worker inherits it."""
    return hasattr(config, "workerinput")


# --- options and session state ---


@dataclasses.dataclass
class _SessionState:
    """Per-session state, held in `config.stash` so nested sessions never share it."""

    recorder: EvalRecorder
    output_file: str | None
    repository_url: str | None
    failed_run_id: str | None = None


_state_key: pytest.StashKey[_SessionState] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("evaltrack", "evaltrack")

    group.addoption(
        "--evaltrack-repository",
        action="store",
        default=None,
        dest="evaltrack_repository",
        help="Where to save the run: a directory or a URL, e.g. ./.evaltrack, "
        "azure://account/container[/prefix] or s3://bucket[/prefix]. Overrides "
        "$EVALTRACK_LOCAL and [tool.evaltrack].local.",
    )

    group.addoption(
        "--evaltrack-run-file",
        action="store",
        default=None,
        dest="evaltrack_run_file",
        help="Write the recorded run as JSON to this path instead of saving to a "
        "repository. Used by CI before `evaltrack push`.",
    )


def _validate_output_path(output_file: str) -> None:
    """Reject a path the plugin cannot write. The nearest directory that already
    exists must be writable."""
    path = Path(output_file)
    if path.is_dir():
        raise pytest.UsageError(
            f"--evaltrack-run-file: {output_file} is a directory, expected a file path"
        )
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise pytest.UsageError(
            f"--evaltrack-run-file: cannot create {output_file}: "
            f"{ancestor} is not a directory"
        )
    if not os.access(ancestor, os.W_OK):
        raise pytest.UsageError(
            f"--evaltrack-run-file: cannot create {output_file}: "
            f"{ancestor} is not writable"
        )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "evaltrack(score_bars=, repeats=, flake_reruns=, reliability_target=, "
        "eval_version=): record the eval this test runs and fail the test when "
        "a case fails or a task or evaluator raises. Each kwarg, with its "
        "default, is in https://github.com/jesrav/evaltrack/blob/main/docs/"
        "configuration.md#marker-keyword-arguments",
    )

    output_file = config.getoption("evaltrack_run_file", default=None)
    cli_repository = config.getoption("evaltrack_repository", default=None)

    if output_file and cli_repository:
        raise pytest.UsageError(
            "choose one of --evaltrack-run-file or --evaltrack-repository, not both"
        )
    if output_file:
        _validate_output_path(output_file)

    # A broken pyproject.toml is a user error, so no traceback.
    try:
        evaltrack_config = load_config(Path(config.rootpath))
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc

    recorder = EvalRecorder(keep_raw_results=evaltrack_config.keep_raw_results)

    # The default target is anchored to the project (nearest pyproject.toml),
    # not to the rootdir.
    repository_url = (
        None
        if output_file
        else resolve_local(
            evaltrack_config, override=cli_repository, start=Path(config.rootpath)
        )
    )
    config.stash[_state_key] = _SessionState(
        recorder=recorder,
        output_file=output_file,
        repository_url=repository_url,
    )


# --- the marker ---


def _reject_bool(value: object) -> object:
    """Refuse a bool where a score bar belongs. `FiniteFloat` takes an int and a
    bool is an int, so `score_bars={"q": True}` would be a bar of 1.0. The counts
    need no guard, since `StrictInt` refuses a bool already."""
    if isinstance(value, bool):
        raise PydanticCustomError("float_type", "Input should be a valid number")
    return value


_ScoreBarValue = Annotated[FiniteFloat, BeforeValidator(_reject_bool)]


class MarkerKwargs(BaseModel):
    """What `@pytest.mark.evaltrack(...)` accepts.

    An unknown kwarg, a bool where a number belongs, and a count that is not an
    integer are all refused.
    """

    model_config = ConfigDict(extra="forbid")

    score_bars: dict[str, _ScoreBarValue] = Field(default_factory=dict)
    repeats: StrictInt = Field(default=1, ge=1)
    flake_reruns: StrictInt = Field(default=0, ge=0)
    reliability_target: FiniteFloat | None = Field(default=None, gt=0, le=1)
    eval_version: str | None = None

    @model_validator(mode="after")
    def _one_direction(self) -> "MarkerKwargs":
        if self.repeats > 1 and self.flake_reruns > 0:
            raise ValueError(
                "repeats= demands consistency and flake_reruns= absorbs "
                "flakiness. A test sets one or the other, not both"
            )
        return self


def _resolve_marker_kwargs(node: pytest.Item) -> dict[str, Any]:
    """Merge the marker chain. Each kwarg comes from the closest marker that sets
    it, `score_bars` as a whole dict."""
    merged: dict[str, Any] = {}
    # `iter_markers` yields closest first.
    for marker in node.iter_markers("evaltrack"):
        for key, value in marker.kwargs.items():
            merged.setdefault(key, value)
    return merged


def _describe_marker_problems(exc: ValidationError) -> str:
    """Every problem in one marker, each as `<kwarg>: <message>`. A typo in a kwarg
    is the common mistake, so an unknown one gets a suggestion."""
    allowed = list(MarkerKwargs.model_fields)
    described: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        if error["type"] == "extra_forbidden":
            match = get_close_matches(loc, allowed, n=1)
            hint = f" Did you mean {match[0]!r}?" if match else ""
            described.append(
                f"unknown kwarg {loc!r} (allowed: {', '.join(allowed)}).{hint}"
            )
        else:
            described.append(f"{loc}: {error['msg']}" if loc else error["msg"])
    return "; ".join(described)


# --- collection ---


def _verify_repository(config: pytest.Config) -> None:
    """Fail before any test runs when the repository cannot take the run."""
    url = config.stash[_state_key].repository_url
    if url is None:
        return
    try:
        repository = open_repository(url)
        repository.verify_available()
        repository.verify_writable()
    except (ValueError, ImportError, RepositoryUnavailableError) as exc:
        raise pytest.UsageError(f"evaltrack repository: {exc}") from exc


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Check the merged marker chain of every marked test, deselected tests
    included, before any test runs. The merge keeps every marker's kwargs, so the
    check still reports a typo in a shadowed module-level marker."""
    if _in_xdist_worker(config):
        # A usage error raised here would crash the controller with an
        # INTERNALERROR naming nothing. Every marked test is refused at setup
        # under xdist anyway, so a worker has nothing to check.
        return
    for item in items:
        markers = list(item.iter_markers("evaltrack"))
        for marker in markers:
            if marker.args:
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.evaltrack takes no positional "
                    "arguments, only keywords"
                )
        if not markers:
            continue
        try:
            MarkerKwargs(**_resolve_marker_kwargs(item))
        except ValidationError as exc:
            raise pytest.UsageError(
                f"{item.nodeid}: invalid @pytest.mark.evaltrack marker: "
                f"{_describe_marker_problems(exc)}"
            ) from exc


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check the repository once the final selection is known. A session with no
    marked tests does not contact it."""
    if session.config.getoption("collectonly"):
        return
    if _in_xdist_worker(session.config):
        # A usage error raised here would crash the controller with an
        # INTERNALERROR naming nothing. No worker saves anyway, since every
        # marked test is refused before it evaluates.
        return
    if any(item.get_closest_marker("evaltrack") for item in session.items):
        _verify_repository(session.config)


# --- one test ---


_XDIST_REFUSAL = (
    "evaltrack does not support pytest-xdist: each worker records its own "
    "partial run, so the saved run would silently drop the tests the other "
    "workers ran. Run the marked tests in a session of their own, without "
    "-n or --dist (for example `pytest -m evaltrack`), and exclude them from "
    'the distributed one with `-m "not evaltrack"`.'
)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Fail, not skip, a marked test on a pytest-xdist worker.

    Per test, because an exception from a worker's collection crashes the controller.
    """
    if item.get_closest_marker("evaltrack") is None:
        return
    if not _in_xdist_worker(item.config):
        return
    pytest.fail(_XDIST_REFUSAL, pytrace=False)


@pytest.fixture(autouse=True)
def _evaltrack_context(  # pyright: ignore[reportUnusedFunction]
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Bind a `MarkedTest` to a test carrying the marker. The binding covers one
    test, from its setup to its teardown, so an eval belongs to the test that ran
    it."""
    node = request.node
    if node.get_closest_marker("evaltrack") is None:
        yield
        return

    state = request.config.stash[_state_key]
    recorder = state.recorder

    kwargs = MarkerKwargs(**_resolve_marker_kwargs(node))
    marked_test = MarkedTest(
        node.nodeid,
        recorder=recorder,
        settings=MarkerSettings(
            score_bars=kwargs.score_bars,
            repeats=kwargs.repeats,
            flake_reruns=kwargs.flake_reruns,
            reliability_target=kwargs.reliability_target,
            eval_version=kwargs.eval_version,
        ),
    )

    with bind_marked_test(marked_test):
        yield


def _describe_missing_eval(nodeid: str) -> str:
    """The failure text for a marked test that passed without evaluating."""
    return (
        f"evaltrack: {nodeid} is marked @pytest.mark.evaltrack but recorded no "
        "eval, so any score bars on the marker were never checked and nothing "
        "was saved for this test.\n"
        "Run the eval from the test body itself with evaltrack.run(), or drop "
        "the marker if this test is not an eval.\n"
        "evaltrack is bound to a test from the start of its setup to the end of "
        "the test. An eval run from a fixture shared with other tests, or from a "
        "thread the test starts, happens outside that and does not count."
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Record a marked test's pytest outcome, and fail one that evaluated nothing.

    Runs after every phase, including a setup that failed, so a test that never
    reached its body is still recorded.
    """
    report = yield
    if item.get_closest_marker("evaltrack") is None:
        return report
    recorder = item.config.stash[_state_key].recorder

    # The nodeid keys the recorder, so two same-named tests in different
    # modules do not collide.
    nodeid = item.nodeid
    recorder.set_test_file(nodeid, nodeid.split("::", 1)[0])
    test_fn = getattr(item, "obj", None)
    if test_fn is not None:
        recorder.set_test_docstring(nodeid, inspect.getdoc(test_fn))

    # Setup records an outcome too, so a broken fixture gets an outcome.
    if call.when == "setup":
        if report.skipped:
            recorder.set_test_outcome(nodeid, "skipped")
        elif report.failed:
            recorder.set_test_outcome(nodeid, "errored")
        return report

    if call.when == "teardown":
        # Only a pass is changed: any other outcome came from the test itself,
        # so a failing teardown must not overwrite it.
        if report.failed and recorder.get_test_outcome(nodeid) == "passed":
            recorder.set_test_outcome(nodeid, "errored")
        return report

    if call.when != "call":
        return report

    # pytest files an xfailed test under `skipped` and a non-strict xpass under
    # `passed`. A strict xpass has no `wasxfail` and falls through to `failed`.
    if hasattr(report, "wasxfail"):
        recorder.set_test_outcome(nodeid, "xpassed" if report.passed else "xfailed")
    elif report.passed:
        if recorder.has_evaluated(nodeid):
            recorder.set_test_outcome(nodeid, "passed")
        else:
            report.outcome = "failed"
            report.longrepr = _describe_missing_eval(nodeid)
            # `errored` rather than `failed`, since no eval reached a verdict.
            recorder.set_test_outcome(nodeid, "errored")
    elif report.skipped:
        recorder.set_test_outcome(nodeid, "skipped")
    # `pytest.fail()` raises `Failed`, not `AssertionError`. Any other exception
    # is a crash.
    elif call.excinfo is not None and not isinstance(
        call.excinfo.value, (AssertionError, pytest.fail.Exception)
    ):
        recorder.set_test_outcome(nodeid, "errored")
    else:
        recorder.set_test_outcome(nodeid, "failed")
    return report


# --- saving the run ---


def _detect_run_context(config: pytest.Config) -> RunContext:
    """Detect the context from git and the environment. It is rooted at the tested
    project, not at the directory pytest ran from."""
    return detect(
        git_runner=config.stash.get(_git_runner_key, None),
        dirty_check=config.stash.get(_dirty_check_key, None),
        cwd=config.rootpath,
    )


def _write_run_file(recorder: EvalRecorder, path: Path) -> None:
    """Write to a uniquely named sibling file, then rename it. A crash then cannot
    leave torn JSON, and a planted symlink cannot redirect the write."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # A run holds prompts and model output, so it is owner-only, like a
        # run the plugin saves to a file repository.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as staged:
            staged.write(dump_run_json(recorder.to_run_record()))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _save_recorded_run(config: pytest.Config) -> bool:
    """Save the recorded run. Returns False when a run was recorded and could not
    be saved."""
    state = config.stash[_state_key]
    recorder = state.recorder
    if not recorder.has_rounds:
        return True

    # Only now, with an eval to save, is the git context worth a subprocess.
    recorder.context = _detect_run_context(config)

    if state.output_file:
        run_path = Path(state.output_file)
        try:
            _write_run_file(recorder, run_path)
        except Exception as exc:
            print(
                f"\nevaltrack: could not write run {recorder.id} to {run_path}. "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return False
        print(f"\nevaltrack: wrote run to {run_path}")
        return True

    url = state.repository_url
    if url is None:
        return True
    # Reported, because a silent fallback hides a broken CI setup.
    try:
        repository = open_repository(url)
        repository.save_run(recorder.to_run_record())
    except Exception as exc:
        print(
            f"\nevaltrack: could not save run {recorder.id} to {url}. "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False
    print(f"\nevaltrack: saved run {recorder.id} to {url}")

    # Stashed rather than printed, because a print here lands above the traceback.
    if recorder.has_failures:
        state.failed_run_id = recorder.id
    return True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    if not _save_recorded_run(session.config):
        # Reported rather than raised, because an exception here loses the
        # FAILURES section.
        # An already-failing status stands.
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR


# pluggy passes hook arguments positionally, so they cannot be keyword-only.
def pytest_terminal_summary(  # noqa: PLR0917
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,  # noqa: ARG001
    config: pytest.Config,
) -> None:
    """Show the dashboard hint in its own section near the failure list."""
    run_id = config.stash[_state_key].failed_run_id
    if run_id is None:
        return
    terminalreporter.write_sep("=", "evaltrack", cyan=True, bold=True)
    terminalreporter.write_line(
        f"Run {run_id} had failures. Inspect it in the dashboard:"
    )
    terminalreporter.write_line(
        f"    evaltrack ui --run-id {run_id}", cyan=True, bold=True
    )
