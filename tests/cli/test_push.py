"""`evaltrack push`, which saves a run file into a repository, and the ref
metadata that can ride along."""

import json
import sys
from pathlib import Path

import pytest

from evaltrack.cli import main
from evaltrack.core.run_record import RUN_SCHEMA_VERSION
from evaltrack.repositories import open_repository

from .helpers import configure_repositories, run_cli, write_run_file


def test_push_saves_run_to_repository(tmp_path: Path) -> None:
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    code = main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
        ]
    )
    assert code == 0
    repo = open_repository(str(repository_dir))
    assert repo.load_run(run_file.run_id) is not None


def test_push_with_ref_points_ref_at_run(tmp_path: Path) -> None:
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    code = main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
            "--ref",
            "pr/42",
            "--commit",
            "merge-sha",
            "--pr",
            "42",
        ]
    )
    assert code == 0
    repo = open_repository(str(repository_dir))
    ref = repo.get_ref("pr/42")
    assert ref is not None
    assert ref.run_id == run_file.run_id
    [entry] = list(repo.get_reflog("pr/42"))
    assert entry.commit == "merge-sha"
    assert entry.pr == 42


def test_push_with_an_empty_ref_saves_nothing(tmp_path: Path) -> None:
    """An unset shell variable expands to an empty string, so the documented
    `--ref "$REF"` recipe still passes the flag. A truthiness test reads it as
    absent. The run then lands with nothing that points at it, and the command
    exits 0."""
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    url = str(repository_dir)
    code = main(
        ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", ""]
    )
    assert code == 2, "an empty --ref is a user error, not an absent flag"
    assert open_repository(url).load_run(run_file.run_id) is None


def test_push_with_an_empty_repository_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A CI secret that is not set expands to an empty string, so the
    `--repository "$EVALTRACK_REMOTE"` recipe still passes the flag. Read as a
    path it is the working directory, which would litter the checkout, leave the
    shared remote without the run, and still exit 0."""
    run_file = write_run_file(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)

    result = run_cli(["push", "--run-file", str(run_file.path), "--repository", ""])

    assert result.code == 2, "an empty --repository is a user error, not an absent flag"
    assert list(checkout.iterdir()) == [], (
        "the push must not land in the working directory"
    )
    assert "--repository" in result.err


def test_push_rejects_metadata_when_ref_is_empty(tmp_path: Path) -> None:
    """The empty ref is what to report. A message that `--commit` needs `--ref`
    sends the reader after a flag they did pass."""
    run_file = write_run_file(tmp_path)
    url = f"{tmp_path}/repository"
    argv = ["push", "--run-file", str(run_file.path), "--repository", url, "--ref", ""]
    result = run_cli([*argv, "--commit", "abc"])
    assert result.code == 2
    assert "pass --ref or drop them" not in result.err, (
        "advice to pass --ref would name a flag the user did pass"
    )


def test_push_is_idempotent(tmp_path: Path) -> None:
    """A CI job that retries a push must not fail the second time. The repeat
    stores the same bytes again and moves no ref, which the repository tests
    pin against every backend."""
    run_file = write_run_file(tmp_path)
    args = [
        "push",
        "--run-file",
        str(run_file.path),
        "--repository",
        str(tmp_path / "repository"),
        "--ref",
        "pr/1",
    ]
    assert main(args) == 0
    assert main(args) == 0, "the identical repush must still succeed"


def test_push_preserves_fields_a_newer_evaltrack_recorded(tmp_path: Path) -> None:
    """An older CI image can push a run file recorded by a newer plugin. Fields
    added without a schema bump must land in the stored run, not be silently
    stripped by this version's model on the way through."""
    run_file = write_run_file(tmp_path)
    recorded = json.loads(run_file.path.read_bytes())
    recorded["new_run_field"] = {"from": "the future"}
    recorded["tests"]["test_x"]["new_test_field"] = 1
    run_file.path.write_bytes(json.dumps(recorded).encode())
    repository_dir = tmp_path / "repository"

    code = main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
        ]
    )

    assert code == 0
    saved = json.loads(
        (repository_dir / "runs" / f"{run_file.run_id}.json").read_bytes()
    )
    assert saved["new_run_field"] == {"from": "the future"}
    assert saved["tests"]["test_x"]["new_test_field"] == 1


def test_push_refuses_a_run_stamped_with_another_format(tmp_path: Path) -> None:
    """A newer stamp means an existing field changed meaning, so every later
    read of the saved run refuses it. Push refuses first, before it saves
    anything, with the same upgrade advice and no traceback."""
    run_file = write_run_file(tmp_path)
    recorded = json.loads(run_file.path.read_bytes())
    recorded["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    run_file.path.write_bytes(json.dumps(recorded).encode())
    repository_dir = tmp_path / "repository"

    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
        ]
    )

    assert result.code == 2, (
        "a run from a newer evaltrack is the user's to fix, not a bug"
    )
    assert "error:" in result.err
    assert "run schema" in result.err
    assert "this evaltrack reads" in result.err
    assert not (repository_dir / "runs").exists(), (
        "the refusal comes before anything is saved"
    )


def test_push_does_not_echo_the_contents_of_a_file_that_is_not_json(
    tmp_path: Path,
) -> None:
    """A mistyped --run-file can name an .env file, and the refusal lands in a CI log.
    It has to name the path and say what was wrong, never quote the bytes."""
    secret = "sk-proj-notarealkey1234567890"
    run_path = tmp_path / ".env"
    run_path.write_text(f"OPENAI_API_KEY={secret}\n")

    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_path),
            "--repository",
            f"{tmp_path}/repository",
        ]
    )

    assert result.code == 2, "an unparseable run file is a user error, not a bug"
    assert secret not in result.err
    assert "OPENAI_API_KEY" not in result.err
    assert str(run_path) in result.err
    assert "Invalid JSON" in result.err


def test_push_does_not_echo_the_values_of_a_json_file_that_is_not_a_run(
    tmp_path: Path,
) -> None:
    """Valid JSON gets as far as the model, whose rejection carries the parsed
    values. The field and the reason are enough to act on."""
    secret = "sk-proj-notarealkey1234567890"
    run_path = tmp_path / "secrets.json"
    run_path.write_text(json.dumps({"OPENAI_API_KEY": secret}))

    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_path),
            "--repository",
            f"{tmp_path}/repository",
        ]
    )

    assert result.code == 2
    assert secret not in result.err
    assert "OPENAI_API_KEY" not in result.err
    assert str(run_path) in result.err
    assert "run_schema_version" in result.err
    assert "Field required" in result.err


def test_push_missing_run_file_exits_2(tmp_path: Path) -> None:
    result = run_cli(
        [
            "push",
            "--run-file",
            str(tmp_path / "nope.json"),
            "--repository",
            f"{tmp_path}/repository",
        ]
    )
    assert result.code == 2, "a missing run file is a user error, not a bug"
    assert "not found" in result.err


def test_push_run_directory_is_a_clean_error(tmp_path: Path) -> None:
    """A directory passes the exists() check but cannot be a run file."""
    result = run_cli(
        [
            "push",
            "--run-file",
            str(tmp_path),
            "--repository",
            f"{tmp_path}/repository",
        ]
    )
    assert result.code == 2
    assert "not a file" in result.err
    assert str(tmp_path) in result.err


def test_push_invalid_ref_fails_before_saving_the_run(tmp_path: Path) -> None:
    """An invalid `--ref` must fail before the run is saved. Otherwise the failed
    push leaves a run behind with nothing that points at it. The message must
    state the naming rules rather than repeat the rejected name."""
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
            "--ref",
            "../../evil",
        ]
    )
    assert result.code == 2
    assert "../../evil" in result.err
    assert "lowercase letters, digits" in result.err
    assert not (repository_dir / "runs").exists(), (
        "a failed push must leave no orphan run behind"
    )


def test_push_url_missing_a_slash_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`azure:/acct/evals` used to be read as a relative path: the push exited
    0 into a directory named `azure:` in the CI checkout while the shared
    remote's refs stopped advancing, with every pipeline still green."""
    run_file = write_run_file(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            "azure:/acct/evals",
            "--ref",
            "pr/1",
        ]
    )

    assert result.code == 2, result.err
    assert "missing '://'" in result.err
    assert list(workspace.iterdir()) == [], "a refused push must write nothing"


def test_push_unknown_scheme_exits_2(tmp_path: Path) -> None:
    """A misspelled scheme is a user error. It gets a message that lists the
    known schemes, not a traceback."""
    run_file = write_run_file(tmp_path)
    result = run_cli(
        ["push", "--run-file", str(run_file.path), "--repository", "bogus://x"]
    )
    assert result.code == 2
    assert "error:" in result.err
    assert "unknown repository scheme" in result.err
    assert "'bogus'" in result.err
    assert "known schemes: azure" in result.err


def test_push_rejected_url_does_not_quote_it(tmp_path: Path) -> None:
    """A URL nobody claims can still carry userinfo, so the rejection must
    name the scheme alone and not echo the value."""
    run_file = write_run_file(tmp_path)
    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            "bogus://u:hunter2@acct/container",
        ]
    )
    assert result.code == 2
    assert "hunter2" not in result.err, (
        "the userinfo credential must not leak into the error"
    )


def test_push_missing_extra_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scheme whose optional dependency is not installed prints the ImportError
    message that names the pip extra, instead of a traceback. The test hides the
    azure module, so its import fails."""
    run_file = write_run_file(tmp_path)
    monkeypatch.setitem(sys.modules, "evaltrack.repositories.azure", None)
    result = run_cli(
        ["push", "--run-file", str(run_file.path), "--repository", "azure://x"]
    )
    assert result.code == 2
    assert "error:" in result.err
    assert "evaltrack[azure]" in result.err


def test_push_malformed_pyproject_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pyproject.toml that is not valid TOML is a user error. The message
    names the file, and no parser traceback escapes."""
    run_file = write_run_file(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.evaltrack\n", encoding="utf-8")
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    monkeypatch.chdir(tmp_path)  # no --repository -> push consults the config
    result = run_cli(["push", "--run-file", str(run_file.path)])
    assert result.code == 2
    assert "error:" in result.err
    assert "pyproject.toml" in result.err


@pytest.mark.parametrize(
    ("extra", "required"),
    [
        pytest.param(["--commit", "abc"], "--ref", id="commit-without-ref"),
        pytest.param(["--title", "Add caching"], "--ref", id="title-without-ref"),
        pytest.param(
            ["--ref", "pr/42", "--title", "Add caching"], "--pr", id="title-without-pr"
        ),
    ],
)
def test_push_rejects_metadata_with_nowhere_to_land(
    extra: list[str], required: str, tmp_path: Path
) -> None:
    """`--commit`, `--pr` and `--title` land only on the reflog entry that
    `--ref` writes, and a title only ever shows next to a PR number. A flag
    without what it needs is an error, not a value to discard."""
    run_file = write_run_file(tmp_path)
    result = run_cli(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            f"{tmp_path}/repository",
        ]
        + extra
    )
    assert result.code == 2
    assert required in result.err, "the error names the flag the metadata needs"


def test_push_with_title_records_it_on_the_ref(tmp_path: Path) -> None:
    """`--title` lands on the ref's reflog entry, so the ref carries which PR it
    stands for. The title names the PR. It is not a label on the run."""
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    code = main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
            "--ref",
            "pr/42",
            "--pr",
            "42",
            "--title",
            "Add caching",
        ]
    )
    assert code == 0
    repo = open_repository(str(repository_dir))
    [entry] = list(repo.get_reflog("pr/42"))
    assert entry.pr == 42
    assert entry.title == "Add caching"


@pytest.mark.parametrize("value", ["0", "-5"])
def test_push_rejects_a_pr_number_under_one(
    tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reflog entry is stable once written, so the flag must refuse a PR
    number that names no PR."""
    run_file = write_run_file(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "push",
                "--run-file",
                str(run_file.path),
                "--repository",
                f"{tmp_path}/repository",
                "--ref",
                "pr/1",
                "--pr",
                value,
            ]
        )
    assert excinfo.value.code == 2, "argparse rejects the value as a usage error"
    err = capsys.readouterr().err
    assert "--pr" in err
    assert "at least 1" in err


def test_push_pr_without_title_records_the_number(tmp_path: Path) -> None:
    """`--pr` does not need `--title`. A PR number with no title is valid, and
    the ref records only the number."""
    run_file = write_run_file(tmp_path)
    repository_dir = tmp_path / "repository"
    code = main(
        [
            "push",
            "--run-file",
            str(run_file.path),
            "--repository",
            str(repository_dir),
            "--ref",
            "pr/42",
            "--pr",
            "42",
        ]
    )
    assert code == 0
    repo = open_repository(str(repository_dir))
    [entry] = list(repo.get_reflog("pr/42"))
    assert entry.pr == 42
    assert entry.title is None


@pytest.mark.parametrize(
    ("sources", "winner"),
    [
        pytest.param({"config"}, "config", id="config-only"),
        pytest.param({"env"}, "env", id="env-only"),
        pytest.param({"config", "env"}, "env", id="env-beats-config"),
        pytest.param({"config", "env", "flag"}, "flag", id="flag-beats-both"),
    ],
)
def test_push_resolves_its_target_by_precedence(
    sources: set[str],
    winner: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`--repository` beats $EVALTRACK_REMOTE beats [tool.evaltrack].remote. Only the
    winning target is written, so an overridden one is never quietly touched."""
    run_file = write_run_file(tmp_path)
    targets = {name: tmp_path / name for name in ("config", "env", "flag")}
    if "config" in sources:
        (tmp_path / "pyproject.toml").write_text(
            f'[tool.evaltrack]\nremote = "{targets["config"]}"\n',
            encoding="utf-8",
        )
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    if "env" in sources:
        monkeypatch.setenv("EVALTRACK_REMOTE", str(targets["env"]))
    monkeypatch.chdir(tmp_path)
    args = ["push", "--run-file", str(run_file.path)]
    if "flag" in sources:
        args += ["--repository", str(targets["flag"])]

    assert main(args) == 0

    assert open_repository(str(targets[winner])).load_run(run_file.run_id) is not None
    for name, path in targets.items():
        if name != winner:
            assert not path.exists(), f"wrote to the overridden {name} target"


def test_push_echoes_the_env_remote_it_picked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A target taken from the environment is echoed to stderr, so a push to a
    shared store is never silent about where it went."""
    run_file = write_run_file(tmp_path)
    monkeypatch.setenv("EVALTRACK_REMOTE", f"{tmp_path}/remote")
    monkeypatch.chdir(tmp_path)

    result = run_cli(["push", "--run-file", str(run_file.path)])

    assert result.code == 0
    assert "EVALTRACK_REMOTE" in result.err


def test_no_echo_when_the_resolved_repository_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The echo runs after the open. An echo first would print a URL that the
    open then rejects, and a rejected URL can hold a credential."""
    run_file = write_run_file(tmp_path)
    monkeypatch.setenv("EVALTRACK_REMOTE", "azure://acct/c?sig=SUPERSECRETSIG=")
    monkeypatch.chdir(tmp_path)

    result = run_cli(["push", "--run-file", str(run_file.path)])

    assert result.code == 2
    printed = result.out + result.err
    assert "SUPERSECRETSIG" not in printed, (
        "the credential in the rejected URL must never be printed"
    )
    assert "using repository" not in printed, (
        "the echo must not run for a URL that was turned away"
    )


def test_push_without_repository_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No --repository, no env and no config gives a clear error that names all
    three sources."""
    run_file = write_run_file(tmp_path)
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    monkeypatch.chdir(tmp_path)  # no pyproject

    result = run_cli(["push", "--run-file", str(run_file.path)])

    assert result.code == 2
    assert "--repository" in result.err
    assert "EVALTRACK_REMOTE" in result.err
    assert "tool.evaltrack" in result.err


def test_push_local_writes_to_the_configured_local_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`push` defaults to the remote, so you must be able to name the local one
    without its URL."""
    repos = configure_repositories(tmp_path)
    run_file = write_run_file(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["push", "--run-file", str(run_file.path), "--local"]) == 0

    assert open_repository(repos.local).load_run(run_file.run_id) is not None
    assert open_repository(repos.remote).load_run(run_file.run_id) is None, (
        "a --local push must leave the remote untouched"
    )


def test_push_url_missing_a_slash_in_pyproject_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same malformed URL from the config, which used to be anchored to the
    project before anything could recognise it as a URL."""
    run_file = write_run_file(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        '[tool.evaltrack]\nremote = "azure:/acct/evals"\n', encoding="utf-8"
    )
    monkeypatch.chdir(workspace)

    result = run_cli(["push", "--run-file", str(run_file.path), "--remote"])

    assert result.code == 2, result.err
    assert "missing '://'" in result.err
    assert [p.name for p in workspace.iterdir()] == ["pyproject.toml"], (
        "a refused push must write nothing"
    )
