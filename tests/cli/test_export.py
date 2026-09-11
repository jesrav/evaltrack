"""`evaltrack export`, which prints a stored run as JSON."""

import json
from pathlib import Path

import pytest

from evaltrack.cli import main
from evaltrack.core.recorder import EvalRecorder
from evaltrack.repositories import open_repository

from ..factories import make_round
from .helpers import configure_repositories, run_cli, seed_run, seed_run_in_repo


def test_export_full_run_to_stdout(tmp_path: Path) -> None:
    url = str(tmp_path / "repo")
    run_id = seed_run_in_repo(url)
    result = run_cli(["export", run_id, "--repository", url])
    assert result.code == 0
    data = json.loads(result.out)
    assert data["id"] == run_id
    assert "test_x" in data["tests"]


def test_export_emits_stored_reports_as_recorded(tmp_path: Path) -> None:
    """Export is the debugging path, so it must work on a payload the installed
    pydantic-evals rejects. A parse step first would refuse a report an older
    version wrote, and silently drop fields a newer one added."""
    url = str(tmp_path / "repo")
    rec = EvalRecorder()
    rec.add_round("test_x", make_round())
    run = rec.to_run_record()
    # A shape the installed pydantic-evals rejects: a field it does not know,
    # and a required one missing.
    stored_shape = [{"unknown_field": {"kept": 1}}]
    unparsable = run.model_copy(
        update={
            "tests": {
                "test_x": run.tests["test_x"].model_copy(
                    update={"raw_results": stored_shape}
                )
            }
        }
    )
    open_repository(url).save_run(unparsable)

    result = run_cli(["export", unparsable.id, "--repository", url])
    assert result.code == 0
    emitted = json.loads(result.out)
    assert emitted["tests"]["test_x"]["raw_results"] == stored_shape
    assert open_repository(url).load_run(unparsable.id) is not None


def test_export_missing_run_exits_1(tmp_path: Path) -> None:
    """A missing run is the one outcome that exits 1. A script can swallow it
    and still stop on a crash or a usage error."""
    url = str(tmp_path / "repo")
    seed_run_in_repo(url)  # repo exists, but ask for a different id
    result = run_cli(["export", "01J9Z3QW2KJ5H8VN4TQY7B6MDC", "--repository", url])
    assert result.code == 1, "a run the repository does not hold exits 1"
    assert "not found" in result.err
    assert "Traceback" not in result.err, "a missing run is an outcome, not a crash"


def test_export_does_not_look_beyond_the_repository_it_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run held by the other configured repository is still a miss. The error
    names the repository that was read, so a wrong flag is visible.

    This is also what proves `--local` resolved to the local one. The resolver
    itself is `tests/cli/test_listing.py`'s, tested once for every command.
    """
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    run_id = seed_run(repos.remote)

    result = run_cli(["export", run_id, "--local"])

    assert result.code == 1, "a run held only by the other repository is still a miss"
    assert "not found" in result.err
    assert repos.local in result.err, "the error names the repository that was read"


@pytest.mark.parametrize(
    "argv", [["--help"], ["export", "--help"]], ids=["command-list", "export"]
)
def test_export_help_names_no_runner(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A run keeps whichever runner's own result objects it recorded, so help
    that names one runner describes a runner half the readers do not use."""
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 0, "--help is not a usage error"
    # argparse wraps to the terminal width, so a phrase can span two lines.
    help_text = " ".join(capsys.readouterr().out.split())
    assert "pydantic-evals" not in help_text
