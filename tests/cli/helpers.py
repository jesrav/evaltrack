"""Shared code for the CLI tests.

Every test here runs `evaltrack.cli.main` in process. `run_cli` captures what
one invocation prints, so a test reads `(code, out, err)` instead of wiring its
own redirects. The run builders live here because most subcommands need a
repository with something in it.
"""

import io
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ulid import ULID

from evaltrack.cli import main
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_context import RunContext
from evaltrack.core.run_record import dump_run_json
from evaltrack.repositories import open_repository

from ..factories import make_eval_run, make_round


@dataclass(frozen=True)
class CliResult:
    """What one CLI invocation exited with and printed."""

    code: int
    out: str
    err: str

    @property
    def out_lines(self) -> list[str]:
        """The non-empty stdout lines, for assertions over a listing."""
        return [line for line in self.out.splitlines() if line.strip()]


def run_cli(argv: list[str]) -> CliResult:
    """Run one CLI invocation and capture what it exited with and printed.

    Only for arguments that reach `main`'s own handling. An argparse-level
    rejection raises SystemExit instead of returning, so those tests use
    `pytest.raises(SystemExit)` with capsys.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return CliResult(code, out.getvalue(), err.getvalue())


@dataclass(frozen=True)
class RunFile:
    """A recorded run written to disk, as `push --run-file` consumes it."""

    path: Path
    run_id: str


def write_run_file(tmp_path: Path, *, commit: str | None = "abc") -> RunFile:
    """Write a synthetic run JSON file."""
    rec = EvalRecorder(RunContext(commit=commit))
    rec.add_round("test_x", make_round())
    path = tmp_path / "run.json"
    path.write_bytes(dump_run_json(rec.to_run_record()))
    return RunFile(path, rec.id)


def seed_run(
    url: str,
    *,
    minutes_ago: int = 0,
    commit: str | None = "a1b2c3d4e5f6",
    worktree_dirty: bool | None = None,
    branch: str | None = None,
    failed: int = 0,
    passed: int = 1,
    skipped: int = 0,
    xfailed: int = 0,
) -> str:
    """Save a run carrying the metadata a listing prints. Return its id.

    `minutes_ago` dates the run. Two runs saved in the same millisecond get ids
    with no defined order between them, so a test about ordering has to date
    them itself."""
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    labels = {"branch": branch} if branch else {}
    rec = EvalRecorder(
        RunContext(commit=commit, worktree_dirty=worktree_dirty, labels=labels),
        run_id=str(ULID.from_datetime(when)),
        created_at=when,
    )
    for index in range(passed):
        rec.set_test_outcome(f"test_ok_{index}", "passed")
    for index in range(failed):
        rec.set_test_outcome(f"test_bad_{index}", "failed")
    for index in range(skipped):
        rec.set_test_outcome(f"test_skip_{index}", "skipped")
    for index in range(xfailed):
        rec.set_test_outcome(f"test_xfail_{index}", "xfailed")
    run = rec.to_run_record()
    open_repository(url).save_run(run)
    return run.id


def save_old_run(repository_url: str, days_ago: int = 60) -> str:
    """Save an unreferenced run whose ULID is `days_ago` days old."""
    repo = open_repository(repository_url)
    ts = int(time.time() * 1000) - days_ago * 86_400_000
    run = make_eval_run(run_id=str(ULID.from_timestamp(ts / 1000)))
    repo.save_run(run)
    return run.id


def seed_run_in_repo(url: str, *, keep_raw_results: bool = True) -> str:
    """Record a run and save it into the repository at `url`. Return its id."""
    rec = EvalRecorder(keep_raw_results=keep_raw_results)
    rec.add_round("test_x", make_round())
    run = rec.to_run_record()
    open_repository(url).save_run(run)
    return run.id


@dataclass(frozen=True)
class ConfiguredRepositories:
    """The repository URLs `configure_repositories` wrote to the config."""

    local: str
    remote: str


def configure_repositories(tmp_path: Path) -> ConfiguredRepositories:
    """Point `[tool.evaltrack]` at a local and a remote repository under
    `tmp_path`."""
    local = f"{tmp_path}/local"
    remote = f"{tmp_path}/remote"
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{local}"\nremote = "{remote}"\n',
        encoding="utf-8",
    )
    return ConfiguredRepositories(local, remote)


def configure_local(tmp_path: Path, url: str) -> None:
    """Point `[tool.evaltrack].local` at `url` and configure no remote."""
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{url}"\n', encoding="utf-8"
    )
