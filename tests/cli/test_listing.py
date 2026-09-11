"""`evaltrack runs` and `evaltrack refs`, plus how any command names the
repository it acts on (`--repository` / `--local` / `--remote`)."""

from pathlib import Path

import pytest

from evaltrack.cli import main
from evaltrack.repositories import open_repository

from .helpers import (
    configure_repositories,
    run_cli,
    seed_run,
)

# runs


def test_a_run_with_nothing_recorded_says_so(tmp_path: Path) -> None:
    """A run that recorded no test is a real state: a session where every eval
    was deselected still saves. `0/0 failed` would read as a clean run, so the
    line says there was nothing to run."""
    url = f"{tmp_path}/repository"
    seed_run(url, passed=0, commit=None)

    result = run_cli(["runs", "--repository", url])

    assert result.code == 0
    assert "no tests" in result.out
    assert "no-commit" in result.out, "an uncommitted checkout still names itself"


def test_runs_lists_newest_first_with_what_it_takes_to_pick_one(
    tmp_path: Path,
) -> None:
    """Without the dashboard this listing is the only view of a repository, so
    a line must carry the id to act on, the checkout, and the failure count."""
    url = f"{tmp_path}/repository"
    older = seed_run(url, minutes_ago=5, commit="1111111111", branch="main", passed=3)
    newer = seed_run(
        url,
        commit="2222222222",
        worktree_dirty=True,
        branch="feature/x",
        failed=2,
        passed=1,
    )

    result = run_cli(["runs", "--repository", url])
    lines = result.out_lines

    assert result.code == 0
    assert newer in lines[0]
    assert "22222222-dirty" in lines[0]
    assert "2/3 failed" in lines[0]
    assert "(feature/x)" in lines[0]
    assert older in lines[1]
    assert "3 tests, none failed" in lines[1]


def test_a_clean_run_does_not_count_skips_as_passes(tmp_path: Path) -> None:
    """The summary counts failures, not passes. A skipped or expected-failure
    test is neither, so a line that read `3 passed` here overstated the run."""
    url = f"{tmp_path}/repository"
    seed_run(url, passed=1, skipped=1, xfailed=1)

    result = run_cli(["runs", "--repository", url])

    assert result.code == 0
    assert "3 tests, none failed" in result.out
    assert "passed" not in result.out


def test_runs_prints_a_commit_and_branch_without_their_control_characters(
    tmp_path: Path,
) -> None:
    """A run carries the commit and branch of the checkout it ran on, so escape
    sequences in either must not reach the terminal raw."""
    url = f"{tmp_path}/repository"
    seed_run(url, commit="\x1b[31mred", branch="\x1b[2K\rmain")

    result = run_cli(["runs", "--repository", url])

    assert result.code == 0
    assert "?[31mre" in result.out
    assert "(?[2K?main)" in result.out


def test_runs_notes_an_empty_repository(tmp_path: Path) -> None:
    """An empty listing and a lost history look the same, so say which it is."""
    url = f"{tmp_path}/repository"

    result = run_cli(["runs", "--repository", url])

    assert result.code == 0
    assert result.out.strip() == "", "the note goes to stderr, stdout is the listing"
    assert "holds no runs yet" in result.err


@pytest.mark.parametrize("flag", ["--local", "--remote"])
def test_runs_lists_only_the_repository_the_flag_names(
    flag: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A role name for a configured repository must list that one and no other.
    A listing merged from both would not say where a run lives."""
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    local_run = seed_run(repos.local)
    remote_run = seed_run(repos.remote)
    wanted, other = (
        (local_run, remote_run) if flag == "--local" else (remote_run, local_run)
    )

    result = run_cli(["runs", flag])
    lines = result.out_lines

    assert result.code == 0
    assert wanted in lines[0]
    assert other not in "\n".join(lines), (
        "the unnamed repository must not leak into the listing"
    )


def test_runs_repository_flag_lists_only_that_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    local_run = seed_run(repos.local)
    remote_run = seed_run(repos.remote)

    result = run_cli(["runs", "--repository", repos.remote])
    lines = result.out_lines

    assert result.code == 0
    assert remote_run in lines[0]
    assert local_run not in "\n".join(lines), (
        "the configured repositories must not leak past --repository"
    )


def test_runs_limit_keeps_the_newest(tmp_path: Path) -> None:
    url = f"{tmp_path}/repository"
    oldest = seed_run(url, minutes_ago=30)
    middle = seed_run(url, minutes_ago=20)
    newest = seed_run(url, minutes_ago=10)

    result = run_cli(["runs", "--repository", url, "--limit", "2"])
    lines = result.out_lines

    assert result.code == 0
    assert newest in lines[0]
    assert middle in lines[1]
    assert oldest not in "\n".join(lines)


def test_runs_limit_takes_any_positive_int(tmp_path: Path) -> None:
    """A limit past the machine word size once failed inside the listing."""
    url = f"{tmp_path}/repository"
    run_id = seed_run(url)

    result = run_cli(["runs", "--repository", url, "--limit", "99999999999999999999"])

    assert result.code == 0
    assert run_id in result.out


@pytest.mark.parametrize("value", ["0", "-1"])
def test_runs_limit_below_one_rejected(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["runs", "--repository", "./.evaltrack", "--limit", value])
    assert excinfo.value.code == 2, "a rejected --limit is a usage error that exits 2"
    err = capsys.readouterr().err
    assert "--limit" in err
    assert "at least 1" in err


# refs


def test_refs_lists_the_baseline_first_then_pull_requests(tmp_path: Path) -> None:
    """A reader looks for the mainline, so it heads the listing whatever order
    the ref names sort in."""
    url = f"{tmp_path}/repository"
    run_id = seed_run(url)
    repository = open_repository(url)
    repository.move_ref("aaa-scratch", run_id)
    repository.move_ref("pr/9", run_id, pr=9, title="Rework the prompt")
    repository.move_ref("baseline", run_id, commit="merge-sha")

    result = run_cli(["refs", "--repository", url])
    lines = result.out_lines

    assert result.code == 0
    assert lines[0].startswith("baseline"), (
        "baseline comes first even though aaa-scratch sorts before it"
    )
    assert run_id in lines[0]
    assert lines[1].startswith("pr/9")
    assert lines[1].endswith("#9 Rework the prompt")
    assert lines[2].startswith("aaa-scratch")


def test_refs_prints_a_title_without_its_control_characters(tmp_path: Path) -> None:
    """A PR title comes from whoever opened the PR, so escape sequences in it
    must not reach the terminal raw."""
    url = f"{tmp_path}/repository"
    run_id = seed_run(url)
    open_repository(url).move_ref("pr/9", run_id, pr=9, title="Fix \x1b[31mred\x1b[0m")

    result = run_cli(["refs", "--repository", url])
    lines = result.out_lines

    assert result.code == 0
    assert lines[0].endswith("#9 Fix ?[31mred?[0m")


def test_refs_notes_an_empty_repository(tmp_path: Path) -> None:
    url = f"{tmp_path}/repository"

    result = run_cli(["refs", "--repository", url])

    assert result.code == 0
    assert result.out.strip() == "", "the note goes to stderr, stdout is the listing"
    assert "holds no refs yet" in result.err


@pytest.mark.parametrize("flag", ["--local", "--remote"])
def test_refs_lists_only_the_repository_the_flag_names(
    flag: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    open_repository(repos.local).move_ref("scratch", seed_run(repos.local))
    open_repository(repos.remote).move_ref("baseline", seed_run(repos.remote))
    wanted = "scratch" if flag == "--local" else "baseline"

    result = run_cli(["refs", flag])
    lines = result.out_lines

    assert result.code == 0
    assert [line.split()[0] for line in lines] == [wanted]


def test_refs_lists_a_ref_whose_history_is_unreadable(tmp_path: Path) -> None:
    """The listing is how a broken ref gets found, so one unreadable history
    must not take the other refs down with it."""
    url = f"{tmp_path}/repository"
    run_id = seed_run(url)
    repository = open_repository(url)
    repository.move_ref("baseline", run_id)
    repository.move_ref("pr/9", run_id, pr=9)
    with (tmp_path / "repository" / "refs" / "pr" / "9.log.jsonl").open("ab") as handle:
        handle.write(b'{"run_id": "01KXB8')

    result = run_cli(["refs", "--repository", url])
    lines = result.out_lines

    assert result.code == 0, "one broken ref must not fail the whole listing"
    assert run_id in lines[0]
    assert lines[1].startswith("pr/9")
    assert "unreadable history" in lines[1]


def test_refs_lists_a_foreign_key_under_refs_as_unreadable(tmp_path: Path) -> None:
    """A key under `refs/` that is not a valid ref name cannot be read as a ref, so
    it lists like an unreadable history, and the refs beside it still list."""
    url = f"{tmp_path}/repository"
    run_id = seed_run(url)
    open_repository(url).move_ref("baseline", run_id)
    (tmp_path / "repository" / "refs" / "Notes.log.jsonl").write_bytes(b"{}\n")

    result = run_cli(["refs", "--repository", url])
    lines = result.out_lines

    assert result.code == 0, "one foreign key must not fail the whole listing"
    assert run_id in lines[0]
    assert lines[1].startswith("Notes")
    assert "unreadable history" in lines[1]


# naming a repository


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["runs"], id="runs"),
        pytest.param(["refs"], id="refs"),
        pytest.param(["export", "01ABC"], id="export"),
    ],
)
def test_a_command_that_needs_a_repository_says_how_to_name_one(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A command acts on one repository. A guess would act on a different store
    as the config changes. The refusal must list every way to name one, because
    the user has nothing else to act on."""
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2, "naming no repository is a usage error that exits 2"
    message = capsys.readouterr().err
    for flag in ("--repository", "--local", "--remote"):
        assert flag in message


@pytest.mark.parametrize(
    ("argv", "url"),
    [
        pytest.param(["runs"], "", id="runs"),
        pytest.param(["refs"], "", id="refs"),
        pytest.param(["export", "01ABC"], "", id="export"),
        pytest.param(["promote", "pr/1"], "", id="promote"),
        pytest.param(["runs"], "  ", id="whitespace-only"),
    ],
)
def test_an_empty_repository_is_refused(
    argv: list[str], url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unset shell variable expands to an empty string, which as a path is
    the working directory. Every command that names a repository must refuse it
    rather than act on the current directory or quietly use the configured
    remote."""
    monkeypatch.chdir(tmp_path)

    result = run_cli([*argv, "--repository", url])

    assert result.code == 2, "an empty --repository is a user error that exits 2"
    assert "--repository" in result.err
    assert list(tmp_path.iterdir()) == [], (
        "nothing may be created in the working directory"
    )


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--local", "--remote"], id="local-and-remote"),
        pytest.param(
            ["--repository", "./.evaltrack", "--local"], id="repository-and-local"
        ),
    ],
)
def test_two_names_for_one_repository_are_refused(
    flags: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Two repositories for one command have no sensible reading, so the pair is
    refused. One of them must not win silently."""
    with pytest.raises(SystemExit) as excinfo:
        main(["runs", *flags])

    assert excinfo.value.code == 2, (
        "naming two repositories is a usage error that exits 2"
    )
    assert "not allowed with" in capsys.readouterr().err


def test_remote_without_a_configured_one_is_a_clean_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--remote` can fail where `--local` cannot, so it must name what to
    set."""
    monkeypatch.chdir(tmp_path)  # no pyproject, and the env is clear

    result = run_cli(["runs", "--remote"])

    assert result.code == 2, "an unconfigured remote is a user error that exits 2"
    assert "EVALTRACK_REMOTE" in result.err
    assert "tool.evaltrack" in result.err


def test_local_falls_back_to_the_project_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--local` always resolves. Without `[tool.evaltrack].local` it is the
    project's default store, which is where the suite saved its runs."""
    (tmp_path / "pyproject.toml").write_text("[tool.evaltrack]\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    run_id = seed_run(f"{tmp_path}/.evaltrack")
    monkeypatch.chdir(tmp_path / "sub")

    result = run_cli(["runs", "--local"])
    lines = result.out_lines

    assert result.code == 0
    assert run_id in lines[0]


def test_local_prefers_the_repository_the_environment_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """$EVALTRACK_LOCAL moves every command that names the local repository, so
    one variable points the whole tool at another store."""
    repos = configure_repositories(tmp_path)
    from_env = f"{tmp_path}/from-env"
    monkeypatch.setenv("EVALTRACK_LOCAL", from_env)
    monkeypatch.chdir(tmp_path)
    config_run = seed_run(repos.local)
    env_run = seed_run(from_env)

    result = run_cli(["runs", "--local"])
    lines = result.out_lines

    assert result.code == 0
    assert env_run in lines[0]
    assert config_run not in "\n".join(lines)


def test_local_from_the_environment_follows_the_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """evaltrack reads a relative path in $EVALTRACK_LOCAL as written, like one
    typed on the command line. An anchor to the project would list a store the
    caller did not name."""
    (tmp_path / "pyproject.toml").write_text("[tool.evaltrack]\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    run_id = seed_run(f"{tmp_path}/sub/elsewhere")
    monkeypatch.setenv("EVALTRACK_LOCAL", "./elsewhere")
    monkeypatch.chdir(tmp_path / "sub")

    result = run_cli(["runs", "--local"])
    lines = result.out_lines

    assert result.code == 0
    assert run_id in lines[0]


@pytest.mark.parametrize(
    ("variable", "flag"),
    [
        pytest.param("EVALTRACK_LOCAL", "--local", id="local"),
        pytest.param("EVALTRACK_REMOTE", "--remote", id="remote"),
    ],
)
def test_a_broken_config_table_is_refused_even_when_the_environment_names_one(
    variable: str, flag: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI and the pytest plugin have to agree about a broken table. A
    misspelled key one of them refuses and the other honours makes the store it
    names look like it lost its runs, so the config is read whether or not the
    environment already named a repository."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.evaltrack]\nlocl = './.evaltrack'\n", encoding="utf-8"
    )
    monkeypatch.setenv(variable, f"{tmp_path}/from-env")
    monkeypatch.chdir(tmp_path)

    result = run_cli(["runs", flag])

    assert result.code == 2, "an invalid config table is a user error that exits 2"
    assert "locl" in result.err
    assert "pyproject.toml" in result.err
