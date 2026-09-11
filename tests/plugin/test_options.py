"""Where a run is saved.

Covers `--evaltrack-run-file`, `--evaltrack-repository`, and the precedence
between the flag, the environment and the config. Also the startup checks on
the save target, how a failed save is reported, and the refusal of eval tests
under pytest-xdist.
"""

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import parse_run_json
from evaltrack.pytest_plugin import (
    _in_xdist_worker,  # pyright: ignore[reportPrivateUsage]
    _validate_output_path,  # pyright: ignore[reportPrivateUsage]
)

from ..fakes import MemoryStore, RaisingStore, mount_fake_azure
from .helpers import (
    EVAL_TEST_SOURCE,
    SENTINEL_TEST_SOURCE,
    assert_aborted_before_any_test,
    find_nodeid,
    make_eval_source,
    read_run,
    read_stored_attempts,
    run_pytest,
)

pytest_plugins = ["pytester"]


# --- which source names the save target ---


def test_the_flag_beats_the_environment_and_the_config(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--evaltrack-repository` beats $EVALTRACK_LOCAL beats
    [tool.evaltrack].local. The run lands in the flag's store and the overridden
    ones are never touched. A relative flag follows the directory pytest runs
    in, since anchoring it to the project would save to a store the caller did
    not name.

    The ladder itself is covered in `tests/test_config.py`. This session
    pins the option the plugin feeds into it.
    """
    monkeypatch.setenv("EVALTRACK_LOCAL", "./from-env")
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]

        [tool.evaltrack]
        local = "./from-config"
        """
    )
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)

    result = run_pytest(pytester, "--evaltrack-repository=./from-flag")

    result.assert_outcomes(passed=1)
    assert len(list((pytester.path / "from-flag" / "runs").glob("*.json"))) == 1
    assert not (pytester.path / "from-env").exists()
    assert not (pytester.path / "from-config").exists()


def test_the_default_save_target_is_dot_evaltrack_under_the_project(
    tmp_path: Path,
) -> None:
    """With no source set at all the run goes to the built-in default, anchored
    to the project rather than to the directory pytest ran in, so every command
    names the same store however pytest was invoked."""
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n", encoding="utf-8"
    )
    (project / "test_eval.py").write_text(EVAL_TEST_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "../test_eval.py", "-p", "no:cacheprovider"],
        cwd=project / "sub",
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("EVALTRACK_")},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((project / ".evaltrack" / "runs").glob("*.json"))) == 1
    assert not (project / "sub" / ".evaltrack").exists()


# --- option resolution ---


def test_provenance_names_recorded(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """pydantic-evals names an experiment from `name=` and falls back to the task
    function's name. Either way the runner's word for it reaches `details`, which
    does not say which one happened."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=make_eval_source(
            name="test_defaulted",
            preamble="""\
def _dataset():
    return Dataset(name='qa-set', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
""",
            body="evaltrack.run(lambda: asyncio.run(_dataset().evaluate(_task)))",
        )
        + """\

@pytest.mark.evaltrack
def test_named():
    evaltrack.run(lambda: asyncio.run(_dataset().evaluate(_task, name='exp-1')))
"""
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)
    data = read_run(out)
    defaulted = data["tests"][find_nodeid(data, "test_defaulted")]["details"]
    assert defaulted["experiment"] == "_task", (
        "with no name= the report carries the task function's, and says nothing "
        "about which it is"
    )
    named = data["tests"][find_nodeid(data, "test_named")]["details"]
    assert named["experiment"] == "exp-1"


def test_output_creates_parent_dirs(pytester: pytest.Pytester, tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "run.json"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert out.exists()


def test_output_write_leaves_no_tmp_file(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The atomic write stages through a sibling temp file. A successful run
    must publish the run file and leave nothing else behind."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert [p.name for p in tmp_path.iterdir()] == ["run.json"]
    assert find_nodeid(read_run(out), "test_records_eval")


def test_unserializable_user_values_survive_the_save(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """pydantic-evals lets task output and case metadata be any object, so the
    session-end save meets values JSON cannot hold. It must cost those values
    and not the run, which can cost real money to produce. What each one
    degrades to is checked directly where that rule lives."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=make_eval_source(
            name="test_opaque",
            preamble="""\
class _Opaque:
    def __init__(self, x):
        self.x = x
    def __repr__(self):
        return f"_Opaque(x={self.x})"
""",
            task="return _Opaque(2)",
            body="""\
dataset = Dataset(
    name='d',
    cases=[Case(name='c', inputs='x', metadata=_Opaque(1))],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert "INTERNALERROR" not in result.stdout.str()
    assert find_nodeid(read_run(out), "test_opaque")
    assert parse_run_json(out.read_bytes()).tests, (
        "the degraded run must still load back through the schema"
    )


# An ordinary failing test, appended so the session has a FAILURES section the
# guard must not lose.
_PLAIN_FAILURE = """\
def test_plain_failure():
    assert False
"""


# Task outputs the run's JSON cannot hold as text: raw bytes (an image payload)
# and a lone surrogate (a sloppy decode of LLM output). These degrade and the
# run survives. Binary content becomes a sha256 and size fingerprint, and
# pathological text becomes its repr.
_BINARY_OUTPUT = b"\x89PNG\r\n\x1a\n\xff\xfe"
_SURROGATE_OUTPUT = "response\ud800"

# A task output whose `__repr__` raises, which nothing catches before the
# session-end dump's own repr fallback.
_RAISING_REPR_PREAMBLE = """\
class _Explosive:
    def __repr__(self):
        raise RuntimeError("boom")
"""

# A second eval whose data must survive a sibling's undumpable output. A lost
# run also loses this test's results, which can cost real money.
_HEALTHY_SECOND_EVAL = """\

async def _healthy_task(x):
    return x

@pytest.mark.evaltrack
def test_healthy():
    healthy = Dataset(
        name='d2',
        cases=[Case(name='ok', inputs='y', expected_output='y')],
        evaluators=[_Evaluator()],
    )
    evaltrack.run(lambda: asyncio.run(healthy.evaluate(_healthy_task)))
"""


def test_undumpable_output_degrades_and_the_run_is_written(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An output with no UTF-8 form must cost that one value, not the run. The
    file is written, the healthy sibling's data is intact, and the stored run
    loads back. The surrogate payload runs against the repository target below,
    so both payload kinds and both save targets are covered once each. What each
    payload degrades to is checked directly where that rule lives."""
    task = f"return {_BINARY_OUTPUT!r}"
    out = tmp_path / "run.json"
    source = make_eval_source(name="test_undumpable", task=task) + _HEALTHY_SECOND_EVAL
    pytester.makepyfile(test_x=source)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)
    assert "INTERNALERROR" not in result.stdout.str()
    data = read_run(out)
    assert find_nodeid(data, "test_undumpable")
    healthy = data["tests"][find_nodeid(data, "test_healthy")]["cases"]["ok"]
    assert read_stored_attempts(healthy)[0]["output"] == "y"
    assert parse_run_json(out.read_bytes()).tests, (
        "the degraded run must still load back through the schema"
    )


def test_undumpable_output_still_saves_to_the_repository(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A lone surrogate against the repository target. The run is saved and
    loads back, rather than fails in the save-failure path."""
    task = f"return {_SURROGATE_OUTPUT!r}"
    repository_dir = tmp_path / "repository"
    source = make_eval_source(name="test_undumpable", task=task) + _HEALTHY_SECOND_EVAL
    pytester.makepyfile(test_x=source)
    result = run_pytest(pytester, f"--evaltrack-repository={repository_dir}")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*saved run*"])
    run_files = list((repository_dir / "runs").glob("*.json"))
    assert len(run_files) == 1
    data = read_run(run_files[0])
    assert find_nodeid(data, "test_undumpable")
    healthy = data["tests"][find_nodeid(data, "test_healthy")]["cases"]["ok"]
    assert read_stored_attempts(healthy)[0]["output"] == "y"
    assert parse_run_json(run_files[0].read_bytes()).tests, (
        "the degraded run must still load back through the schema"
    )


def test_an_output_whose_repr_raises_degrades_and_the_run_is_written(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The dump's repr fallback is its last resort, so a `__repr__` that raises
    would otherwise fail the write and lose every eval in the session. It costs
    that one value instead: the file is written, the healthy sibling's data is
    intact, and the run loads back."""
    out = tmp_path / "run.json"
    source = (
        make_eval_source(
            name="test_hostile",
            preamble=_RAISING_REPR_PREAMBLE,
            task="return _Explosive()",
        )
        + _HEALTHY_SECOND_EVAL
    )
    pytester.makepyfile(test_x=source)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)
    assert "INTERNALERROR" not in result.stdout.str()
    data = read_run(out)
    hostile = data["tests"][find_nodeid(data, "test_hostile")]["cases"]["c"]
    assert read_stored_attempts(hostile)[0]["output"] == "<unrepresentable _Explosive>"
    healthy = data["tests"][find_nodeid(data, "test_healthy")]["cases"]["ok"]
    assert read_stored_attempts(healthy)[0]["output"] == "y"
    assert parse_run_json(out.read_bytes()).tests, (
        "the degraded run must still load back through the schema"
    )


def test_failed_output_write_keeps_the_test_summary(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A write that fails at session end must be reported like a failed
    repository save. An exception out of the hook costs the FAILURES section and
    the pass/fail counts, which pytest writes later."""
    out = tmp_path / "run.json"
    blocker = f"\n\ndef test_block_the_output_path():\n    pathlib.Path({str(out)!r}).mkdir()\n"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE + blocker)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*2 passed*"])
    result.stderr.fnmatch_lines([f"*could not write run*{out}*"])
    assert "INTERNALERROR" not in result.stdout.str()
    assert result.ret == pytest.ExitCode.INTERNAL_ERROR, (
        "the tests all passed, so the failed save is what fails the session"
    )


def test_blocked_output_parent_keeps_the_test_summary(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The configure-time check passes while the parent directory is only
    missing, so a file planted at its path during the session surfaces at the
    session-end mkdir. That failure must land in the write-failure path like any
    other, and leave pytest its report."""
    out = tmp_path / "out" / "run.json"
    blocker = (
        f"\n\ndef test_block_the_parent_path():\n"
        f"    pathlib.Path({str(out.parent)!r}).write_text('a file, not a directory')\n"
    )
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE + blocker + "\n" + _PLAIN_FAILURE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(
        ["*=== FAILURES ===*", "*test_plain_failure*", "*1 failed, 2 passed*"]
    )
    result.stderr.fnmatch_lines([f"*could not write run*{out}*"])
    assert "INTERNALERROR" not in result.stdout.str()
    assert not out.exists()
    assert result.ret == pytest.ExitCode.TESTS_FAILED, (
        "the test verdict is what the user came for, so it keeps the exit status"
    )


# --- what makes an output path usable ---


def test_a_missing_parent_directory_is_accepted(tmp_path: Path) -> None:
    """The write creates the parents it needs, so only the nearest existing
    ancestor has to be usable."""
    _validate_output_path(str(tmp_path / "nested" / "deeper" / "run.json"))


def test_a_directory_as_the_output_path_is_refused(tmp_path: Path) -> None:
    """A directory would only fail at the session-end rename."""
    with pytest.raises(pytest.UsageError, match="is a directory"):
        _validate_output_path(str(tmp_path))


def test_a_file_where_a_parent_directory_is_needed_is_refused(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    with pytest.raises(pytest.UsageError, match="is not a directory"):
        _validate_output_path(str(blocker / "run.json"))


def test_an_unwritable_ancestor_is_refused(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        if os.access(locked, os.W_OK):
            # Typically root. The permission bits do not bind, `os.access` says
            # so, and the write succeeds, so there is nothing to reject.
            pytest.skip("permission bits are not enforced for this user")
        with pytest.raises(pytest.UsageError, match="is not writable"):
            _validate_output_path(str(locked / "run.json"))
    finally:
        locked.chmod(0o700)


def test_an_unusable_output_path_aborts_before_any_test_runs(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """What the checks above are for. An unusable path must be refused at
    configure time. Found at the final write instead, it costs a full suite run
    and then loses the recorded run."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={blocker / 'run.json'}")
    assert_aborted_before_any_test(result, pytester)
    assert "INTERNALERROR" not in result.stdout.str()


def test_output_and_repository_are_mutually_exclusive(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(
        pytester,
        f"--evaltrack-run-file={tmp_path}/out.json",
        f"--evaltrack-repository={tmp_path}/repository",
    )
    assert "not both" in result.stderr.str().lower()


def test_repository_url_saves_run(pytester: pytest.Pytester, tmp_path: Path) -> None:
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    repository_dir = tmp_path / "repository"
    result = run_pytest(
        pytester,
        f"--evaltrack-repository={repository_dir}",
    )
    result.assert_outcomes(passed=1)
    runs = list((repository_dir / "runs").glob("*.json"))
    assert len(runs) == 1
    data = read_run(runs[0])
    assert find_nodeid(data, "test_records_eval")


def test_the_resolved_save_target_is_where_the_run_lands(
    pytester: pytest.Pytester,
) -> None:
    """The one session behind the resolution rules above. With nothing set, the
    default is not only what the resolver returns but the directory the session
    actually writes into."""
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester)
    result.assert_outcomes(passed=1)
    runs = list((pytester.path / ".evaltrack" / "runs").glob("*.json"))
    assert len(runs) == 1


def test_runs_land_in_the_project_when_pytest_starts_elsewhere(
    tmp_path: Path,
) -> None:
    """Runs belong to the project, not to the directory pytest started in.
    Otherwise one project's history splits across a store per directory, and a
    reader sees only the half next to where they looked."""
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\n\n[tool.evaltrack]\nlocal = "./.evaltrack"\n',
        encoding="utf-8",
    )
    (project / "test_eval.py").write_text(EVAL_TEST_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "../test_eval.py", "-p", "no:cacheprovider"],
        cwd=project / "sub",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((project / ".evaltrack" / "runs").glob("*.json"))) == 1
    assert not (project / "sub" / ".evaltrack").exists()


def test_default_store_follows_the_project_not_the_rootdir(tmp_path: Path) -> None:
    """A package with its own pytest config puts the rootdir below the project.
    Nothing outside pytest knows that rootdir, so a store anchored there writes
    runs where no other command looks for them."""
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\n', encoding="utf-8"
    )
    (project / "pkg" / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (project / "pkg" / "test_eval.py").write_text(EVAL_TEST_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_eval.py", "-p", "no:cacheprovider"],
        cwd=project / "pkg",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((project / ".evaltrack" / "runs").glob("*.json"))) == 1
    assert not (project / "pkg" / ".evaltrack").exists()


def test_unknown_config_key_aborts_the_session(pytester: pytest.Pytester) -> None:
    """An ignored misspelled key makes the store it names look like it lost
    its runs. The usage error names the key."""
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]

        [tool.evaltrack]
        locl = "./.evaltrack"
        """
    )
    result = run_pytest(pytester)
    assert_aborted_before_any_test(result, pytester)
    assert "invalid [tool.evaltrack]" in result.stderr.str()
    assert "locl" in result.stderr.str()


def test_invalid_config_value_aborts_the_session(
    pytester: pytest.Pytester,
) -> None:
    """A bad value on a known key is a typo in this install's own config, so
    it is a configure-time usage error."""
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]

        [tool.evaltrack]
        local = 123
        """
    )
    result = run_pytest(pytester)
    assert_aborted_before_any_test(result, pytester)
    assert "invalid [tool.evaltrack]" in result.stderr.str()


def test_env_save_target_follows_the_directory_pytest_runs_in(
    tmp_path: Path,
) -> None:
    """evaltrack reads a relative path in $EVALTRACK_LOCAL as written, like one
    typed on the command line. An anchor to the project would save to a store
    the caller did not name."""
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n", encoding="utf-8"
    )
    (project / "test_eval.py").write_text(EVAL_TEST_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "../test_eval.py", "-p", "no:cacheprovider"],
        cwd=project / "sub",
        capture_output=True,
        text=True,
        env={**os.environ, "EVALTRACK_LOCAL": "./elsewhere"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((project / "sub" / "elsewhere" / "runs").glob("*.json"))) == 1
    assert not (project / "elsewhere").exists()


def test_unknown_repository_scheme_fails_loudly(
    pytester: pytest.Pytester,
) -> None:
    """A misspelled scheme must fail before any test runs, as a usage error. A
    failure at the final save instead wastes the whole suite run and loses the
    recorded run."""
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, "--evaltrack-repository=bogus://nope")
    assert_aborted_before_any_test(result, pytester)
    assert "unknown repository scheme: 'bogus'" in result.stderr.str()
    assert "INTERNALERROR" not in result.stdout.str()


def test_an_unknown_repository_scheme_is_ignored_without_a_marked_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL is not parsed until a marked test is selected. A project whose
    pyproject.toml names an `azure://` target, with the extra installed only
    where the evals run, can still run its plain unit tests."""
    monkeypatch.setenv("EVALTRACK_LOCAL", "bogus://x")
    pytester.makepyfile(test_x="def test_plain():\n    assert True\n")
    result = run_pytest(pytester)
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK


def test_a_rejected_repository_url_is_not_quoted_in_the_usage_error(
    pytester: pytest.Pytester,
) -> None:
    """The plugin wraps the rejection in its own usage error, so a URL quoted
    there reaches the CI log. A URL nobody claims can still carry userinfo. The
    backend's own redaction of the URLs it parses is checked where that rule
    lives."""
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(
        pytester, "--evaltrack-repository=bogus://u:hunter2@acct/container"
    )
    assert_aborted_before_any_test(result, pytester)
    assert "hunter2" not in result.stderr.str() + result.stdout.str()
    assert "INTERNALERROR" not in result.stdout.str()


def test_missing_repository_extra_fails_before_any_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheme whose optional dependency is not installed must also fail before
    any test runs, with the message that names the pip extra. The test hides
    the azure module, so its import fails."""
    monkeypatch.setitem(sys.modules, "evaltrack.repositories.azure", None)
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, "--evaltrack-repository=azure://x")
    assert_aborted_before_any_test(result, pytester)
    assert "evaltrack[azure]" in result.stderr.str()


def test_file_repository_blocked_by_file_fails_before_any_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A directory repository that cannot be created must fail before
    any test runs, like a bad `--evaltrack-run-file`. Found at the final save
    instead, it costs a full suite run and then loses the recorded run."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    url = f"{blocker}/repository"
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={url}")
    assert_aborted_before_any_test(result, pytester)
    assert "evaltrack repository: cannot create" in result.stderr.str()
    assert f"{blocker.resolve()} is not a directory" in result.stderr.str()
    assert "INTERNALERROR" not in result.stdout.str()


def test_file_repository_path_that_is_a_file_fails_before_any_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A repository path already occupied by a regular file can never become
    the repository directory. Reject it up front like an uncreatable one."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    url = str(blocker)
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={url}")
    assert_aborted_before_any_test(result, pytester)
    assert "evaltrack repository: " in result.stderr.str()
    assert f"{blocker.resolve()} is not a directory" in result.stderr.str()


def test_unusable_repository_is_ignored_without_a_marked_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A suite that records nothing must not stop for a save target it never
    writes to. Otherwise the plugin, installed as a dev dependency, breaks an
    unrelated suite, and an `azure://` target makes every plain pytest run pay a
    network round trip."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    url = f"{blocker}/repository"
    pytester.makepyfile(test_x="def test_plain():\n    assert True\n")
    result = run_pytest(pytester, f"--evaltrack-repository={url}")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK


def test_unusable_repository_is_ignored_when_every_marked_test_is_deselected(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """Deselection (`-k`, `-m`, another plugin's filter) settles after
    collection. A session that collects an eval test it will not run records
    nothing, so it must not fail on a save target it never writes to."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    url = f"{blocker}/repository"
    pytester.makepyfile(
        test_x=EVAL_TEST_SOURCE + "\ndef test_plain():\n    assert True\n"
    )
    result = run_pytest(pytester, "-k", "plain", f"--evaltrack-repository={url}")
    result.assert_outcomes(passed=1, deselected=1)
    assert result.ret == pytest.ExitCode.OK


def test_unusable_repository_is_ignored_under_collect_only(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`--collect-only` runs nothing and records nothing, so it must not reach
    the save target."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    url = f"{blocker}/repository"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, "--collect-only", f"--evaltrack-repository={url}")
    assert result.ret == pytest.ExitCode.OK, "collection must not touch the save target"


def test_a_malformed_marker_on_a_deselected_test_still_aborts(
    pytester: pytest.Pytester,
) -> None:
    """Marker validation stays collection-wide on purpose. The check is local
    and cheap. Otherwise a typo in a test that `-k` skipped hides until the eval
    suite runs next."""
    source = make_eval_source(marker="@pytest.mark.evaltrack(score_bar={'q': 0.8})")
    pytester.makepyfile(test_x=source + "\ndef test_plain():\n    assert True\n")
    result = run_pytest(pytester, "-k", "plain")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "invalid @pytest.mark.evaltrack marker" in result.stderr.str()


def test_file_repository_creates_missing_parents(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A missing parent directory that can be created is not an error. The save
    creates it, so the configure-time check must let it through."""
    repository_dir = tmp_path / "nested" / "deeper" / "repository"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={repository_dir}")
    result.assert_outcomes(passed=1)
    assert len(list((repository_dir / "runs").glob("*.json"))) == 1


def test_file_repository_unwritable_ancestor_fails_before_any_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An ancestor that exists but is not writable means the session-end mkdir
    would fail. Reject it up front like an uncreatable one."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        if os.access(locked, os.W_OK):
            # Typically root. The permission bits do not bind, `os.access` says
            # so, and the save succeeds, so there is nothing to reject.
            pytest.skip("permission bits are not enforced for this user")
        url = f"{locked}/repository"
        pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
        result = run_pytest(pytester, f"--evaltrack-repository={url}")
        assert_aborted_before_any_test(result, pytester)
        assert "evaltrack repository: cannot create" in result.stderr.str()
        assert f"{locked.resolve()} is not writable" in result.stderr.str()
    finally:
        locked.chmod(0o700)


def test_non_filesystem_repository_skips_path_validation(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository that names no filesystem path must pass the startup
    writability check untouched."""
    url = mount_fake_azure(monkeypatch, MemoryStore())
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={url}")
    result.assert_outcomes(passed=1)


# --- a save target that cannot be reached ---


@pytest.mark.parametrize(
    "error",
    [
        ValueError("container 'wip' does not exist; create it first"),
        RepositoryUnavailableError("no credential available"),
    ],
    ids=["misconfigured", "unreachable"],
)
def test_unavailable_repository_fails_before_any_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A remote repository that cannot serve requests must fail before any test
    runs. Found at the final save instead, it costs a full suite run and then
    loses the recorded run."""

    class _UnavailableStore(MemoryStore):
        def verify_available(self) -> None:
            raise error

    url = mount_fake_azure(monkeypatch, _UnavailableStore())
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={url}")
    assert_aborted_before_any_test(result, pytester)
    assert f"evaltrack repository: {error}" in result.stderr.str()
    assert "INTERNALERROR" not in result.stdout.str()


# --- a failed save ---


def _run_with_failing_save(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    source: str | None = None,
) -> pytest.RunResult:
    """Run one recorded eval against a repository that accepts the startup
    checks and then fails the save."""
    url = mount_fake_azure(
        monkeypatch, RaisingStore(RepositoryUnavailableError("credentials expired"))
    )
    pytester.makepyfile(test_x=source or EVAL_TEST_SOURCE)
    return run_pytest(pytester, f"--evaltrack-repository={url}")


def test_failed_save_still_fails_the_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save that fails must reach the user, or broken CI wiring passes
    unnoticed. The error names its cause, so the user knows what to fix."""
    result = _run_with_failing_save(pytester, monkeypatch)

    assert result.ret == pytest.ExitCode.INTERNAL_ERROR
    result.stderr.fnmatch_lines(
        ["*could not save run*azure://x*RepositoryUnavailableError*"]
    )
    result.stderr.fnmatch_lines(["*credentials expired*"])


def test_failed_save_keeps_the_test_summary(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite can fail a test and fail the save in the same session. The user
    needs both verdicts, so the save error is printed and the session ends
    normally, leaving pytest to write its own report."""
    source = EVAL_TEST_SOURCE + "\n\ndef test_plain_failure():\n    assert False\n"
    result = _run_with_failing_save(pytester, monkeypatch, source)

    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        ["*=== FAILURES ===*", "*test_plain_failure*", "*1 failed, 1 passed*"]
    )
    result.stderr.fnmatch_lines(["*credentials expired*"])
    assert "INTERNALERROR" not in result.stdout.str()
    assert result.ret == pytest.ExitCode.TESTS_FAILED, (
        "the test verdict is what the user came for, so it keeps the exit status"
    )


# --- pytest-xdist (unsupported): eval tests are refused, everything else runs ---


def test_a_process_xdist_did_not_start_is_no_worker(pytester: pytest.Pytester) -> None:
    assert not _in_xdist_worker(pytester.parseconfig())


def test_a_worker_is_recognized_by_its_worker_input(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = pytester.parseconfig()
    monkeypatch.setattr(config, "workerinput", {}, raising=False)
    assert _in_xdist_worker(config)


def test_a_nested_session_on_a_worker_is_no_worker(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker's environment reaches every pytest session it starts. Only the
    worker's own config carries `workerinput`, so only it is refused."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    assert not _in_xdist_worker(pytester.parseconfig())


def test_the_plugin_is_registered_under_the_name_users_disable_it_by() -> None:
    """`-p no:evaltrack` is the documented way out, and the name it takes is
    the `pytest11` entry point rather than anything in this package. Renaming
    the entry point would leave every such invocation with nothing to disable,
    and no other test names it."""
    registered = {
        entry.name for entry in importlib.metadata.entry_points(group="pytest11")
    }
    assert "evaltrack" in registered, f"pytest11 entry points: {sorted(registered)}"


def test_xdist_session_of_unmarked_tests_runs(pytester: pytest.Pytester) -> None:
    """A distributed session with no eval tests runs untouched. The plugin
    auto-loads on install, so anything else would break suites that never
    asked for evaltrack."""
    pytester.makepyfile(test_x="def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    result = run_pytest(pytester, "-n", "2")
    result.assert_outcomes(passed=2)
    assert result.ret == pytest.ExitCode.OK


def test_xdist_session_survives_a_marker_typo_it_does_not_run(
    pytester: pytest.Pytester,
) -> None:
    """A worker cannot report a bad marker, because raising from its collection
    crashes the controller with an INTERNALERROR that names nothing. A
    distributed session of ordinary tests keeps running even when an eval file
    it deselects carries a typo."""
    pytester.makepyfile(
        test_plain="def test_a():\n    pass\n\ndef test_b():\n    pass\n",
        test_evals="import pytest\n\n"
        "@pytest.mark.evaltrack(score_barz={'quality': 0.8})\n"
        "def test_typo():\n    pass\n",
    )
    result = run_pytest(pytester, "-n", "2", "-m", "not evaltrack")
    result.assert_outcomes(passed=2)
    assert result.ret == pytest.ExitCode.OK
    assert "INTERNALERROR" not in result.stdout.str()


def test_xdist_eval_tests_are_refused(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A marked test on a worker fails before its body runs. Each worker would
    otherwise record only its own share of the tests, and that partial run is
    structurally valid, so it would be pushed and promoted like a complete
    one."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, "-n", "2", f"--evaltrack-run-file={out}")
    result.assert_outcomes(errors=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "does not support pytest-xdist" in result.stdout.str()
    assert "INTERNALERROR" not in result.stdout.str()
    assert not (pytester.path / "SENTINEL").exists(), (
        "the refusal must come before any eval body runs"
    )
    assert not out.exists()


def test_xdist_does_not_verify_an_unreachable_repository(
    pytester: pytest.Pytester,
) -> None:
    """A worker never saves, so it never checks the save target either. The
    check raises a usage error, and raising one from a worker crashes the
    controller with an INTERNALERROR that names nothing. The marked tests must
    fail on the xdist refusal instead."""
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(
        pytester, "-n", "2", "--evaltrack-repository=azure://nosuch/container"
    )
    assert "INTERNALERROR" not in result.stdout.str()
    assert "does not support pytest-xdist" in result.stdout.str()
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_xdist_refusal_spares_unmarked_tests_in_the_same_session(
    pytester: pytest.Pytester,
) -> None:
    """Only the marked tests are refused. The rest of a mixed session keeps
    its workers and its verdicts."""
    pytester.makepyfile(
        test_x=EVAL_TEST_SOURCE + "\ndef test_plain():\n    pass\n",
    )
    result = run_pytest(pytester, "-n", "2")
    result.assert_outcomes(passed=1, errors=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_xdist_workers_save_nothing_to_a_repository(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """Without `--evaltrack-run-file` each worker would save its own separate
    partial run to the repository. The refusal keeps every worker from
    recording, so nothing is saved."""
    repository_dir = tmp_path / "repository"
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(pytester, "-n", "2", f"--evaltrack-repository={repository_dir}")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "does not support pytest-xdist" in result.stdout.str()
    assert not (pytester.path / "SENTINEL").exists(), (
        "the refusal must come before any eval body runs"
    )
    assert not (repository_dir / "runs").exists()


def test_xdist_run_named_by_transport_is_refused(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`-n` is only the shorthand. A session that names its transports is
    distributed just the same, and it never sets `numprocesses`."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=SENTINEL_TEST_SOURCE)
    result = run_pytest(
        pytester, "--dist", "load", "--tx", "2*popen", f"--evaltrack-run-file={out}"
    )
    result.assert_outcomes(errors=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "does not support pytest-xdist" in result.stdout.str()
    assert "INTERNALERROR" not in result.stdout.str()
    assert not (pytester.path / "SENTINEL").exists(), (
        "the refusal must come before any eval body runs"
    )
    assert not out.exists()


def test_zero_workers_is_unaffected(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """`-n 0` asks xdist for no distribution, a common way to switch it off in a
    shared config. No worker starts, so the session records as usual."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, "-n", "0", f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert find_nodeid(read_run(out), "test_records_eval")
