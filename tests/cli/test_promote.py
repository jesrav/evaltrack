"""`evaltrack promote`, which moves the mainline."""

from pathlib import Path

import pytest

from evaltrack.cli import main
from evaltrack.repositories import open_repository

from .helpers import run_cli, write_run_file


def test_promote_moves_baseline(tmp_path: Path) -> None:
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    # Stage the run: push it, point pr/7 at it, and record the PR number.
    main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
            "--ref",
            "pr/7",
            "--pr",
            "7",
        ]
    )
    code = main(
        [
            "promote",
            "pr/7",
            "--repository",
            str(repository_dir),
            "--commit",
            "merge-sha",
        ]
    )
    assert code == 0
    repo = open_repository(str(repository_dir))
    baseline = repo.get_ref("baseline")
    assert baseline is not None
    assert baseline.run_id == run_file.run_id
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.commit == "merge-sha"


def test_promote_missing_ref_exits_2(tmp_path: Path) -> None:
    repository_dir = tmp_path / "repository"
    result = run_cli(["promote", "pr/999", "--repository", str(repository_dir)])
    assert result.code == 2
    assert result.err.startswith("error:"), "a clean line, not a traceback"
    assert "pr/999" in result.err, "the message names the ref that resolved to nothing"


def test_promote_cleanup_removes_source_ref(tmp_path: Path) -> None:
    run_file = write_run_file(tmp_path)
    url = f"{tmp_path}/repository"
    main(
        ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", "pr/7"]
    )

    result = run_cli(
        ["promote", "pr/7", "--repository", url, "--commit", "m", "--cleanup"]
    )

    assert result.code == 0
    assert "cleaned up pr/7" in result.out
    repo = open_repository(url)
    assert repo.get_ref("pr/7") is None
    baseline = repo.get_ref("baseline")
    assert baseline is not None
    assert baseline.run_id == run_file.run_id
    assert repo.load_run(run_file.run_id) is not None, (
        "the promoted run survives via baseline"
    )


def test_promote_cleanup_exits_2_when_a_reflog_is_unreadable(tmp_path: Path) -> None:
    """An unreadable history elsewhere leaves every run possibly reachable, so
    the cleanup deletes nothing and the command fails, naming the line to
    repair. The promote itself has landed by then, and a retry after the repair
    finishes the cleanup."""
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    url = str(repository_dir)
    main(
        ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", "pr/7"]
    )
    # Overwrites run.json, which the first push already consumed.
    other = write_run_file(tmp_path)
    main(["push", "--run-file", str(other.path), "--repository", url, "--ref", "keep"])
    with (repository_dir / "refs" / "keep.log.jsonl").open("ab") as handle:
        handle.write(b'{"run_id": "01KXB8')

    result = run_cli(["promote", "pr/7", "--repository", url, "--cleanup"])

    assert result.code == 2, "a cleanup that cannot run is something to fix"
    assert result.err.startswith("error:"), "a clean line, not a traceback"
    assert "refs/keep.log.jsonl" in result.err and "line 2" in result.err, (
        "the message names the file and line to repair"
    )
    assert "cleaned up" not in result.out, (
        "claiming the runs were cleaned up would be false"
    )
    repo = open_repository(url)
    baseline = repo.get_ref("baseline")
    assert baseline is not None and baseline.run_id == run_file.run_id, (
        "the promote landed before the cleanup refused"
    )
    assert repo.get_ref("pr/7") is not None, "the refused cleanup leaves the ref"
    assert repo.load_run(run_file.run_id) is not None, "unprovable runs stay"


@pytest.mark.parametrize("extra_args", [[], ["--cleanup"]], ids=["plain", "cleanup"])
def test_promote_baseline_refused(tmp_path: Path, extra_args: list[str]) -> None:
    """`promote baseline` is a no-op onto itself. With --cleanup it tries to
    delete the baseline. Both refuse with a clean message, not a traceback."""
    repository_dir = tmp_path / "repository"
    open_repository(str(repository_dir))
    result = run_cli(
        ["promote", "baseline", "--repository", str(repository_dir)] + extra_args
    )
    assert result.code == 2
    assert "error: cannot promote 'baseline'" in result.err


def test_promote_defaults_to_config_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_file = write_run_file(tmp_path)
    remote_dir = tmp_path / "remote"
    url = str(remote_dir)
    main(
        ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", "pr/7"]
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nremote = "{url}"\n', encoding="utf-8"
    )
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    monkeypatch.chdir(tmp_path)

    code = main(["promote", "pr/7"])

    assert code == 0
    baseline = open_repository(url).get_ref("baseline")
    assert baseline is not None
    assert baseline.run_id == run_file.run_id


def test_promote_without_repository_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No --repository, no env and no config gives an error. The CLI must not
    guess a local store for a change to shared state."""
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    monkeypatch.chdir(tmp_path)  # no pyproject

    result = run_cli(["promote", "pr/1"])

    assert result.code == 2
    assert "--repository" in result.err
    assert "EVALTRACK_REMOTE" in result.err


def test_promote_prints_a_commit_without_its_control_characters(
    tmp_path: Path,
) -> None:
    """The promoted commit comes from the reflog, which a CI job wrote from a
    merge event, so escape sequences in it must not reach the terminal raw.
    `runs` and `refs` already strip theirs."""
    run_file = write_run_file(tmp_path)
    url = str(tmp_path / "repository")
    main(
        ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", "pr/7"]
    )

    result = run_cli(
        ["promote", "pr/7", "--repository", url, "--commit", "\x1b[31mdeadbeef"]
    )

    assert result.code == 0
    assert "\x1b" not in result.out, "an escape sequence reached the terminal"
    assert "?[31mdeadbeef" in result.out
