"""Loading the `[tool.evaltrack]` config from pyproject.toml, and resolving the
repository a caller acts on from it."""

from pathlib import Path

import pytest

from evaltrack.config import (
    LOCAL_ENV,
    REMOTE_ENV,
    EvaltrackConfig,
    anchor_path,
    load_config,
    project_root,
    resolve_local,
    resolve_remote,
)


def _write_pyproject(directory: Path, body: str) -> None:
    (directory / "pyproject.toml").write_text(body, encoding="utf-8")


def test_load_config_reads_local_and_remote(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """
        [tool.evaltrack]
        local = "./.evaltrack"
        remote = "azure://account/evals"
        """,
    )
    cfg = load_config(tmp_path)
    assert cfg.local == f"{tmp_path}/.evaltrack"
    assert cfg.remote == "azure://account/evals"


def test_load_config_missing_table_is_empty(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "[tool.other]\nkey = 1\n")
    cfg = load_config(tmp_path)
    assert cfg.local is None
    assert cfg.remote is None


def test_load_config_no_pyproject_is_empty(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.local is None
    assert cfg.remote is None


def test_load_config_reads_pr_url_template(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        """
        [tool.evaltrack]
        pr_url_template = "https://github.com/OWNER/REPO/pull/{pr}"
        """,
    )
    cfg = load_config(tmp_path)
    assert cfg.pr_url_template == "https://github.com/OWNER/REPO/pull/{pr}"


def test_load_config_pr_url_template_defaults_to_none(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "[tool.evaltrack]\nlocal = 'azure://account/evals'\n")
    assert load_config(tmp_path).pr_url_template is None


# Every mistake in the table must raise a ValueError that names the
# pyproject.toml it came from. The file is found by a search upward, so a
# message that only says what is wrong does not tell the user which file to fix.
_BAD_CONFIGS = [
    # Invalid TOML, so a caller can report it as a user error rather than leak
    # a tomllib traceback.
    pytest.param("[tool.evaltrack\n", "could not parse", id="malformed-toml"),
    # The template is only useful if it can interpolate the PR number.
    pytest.param(
        '[tool.evaltrack]\npr_url_template = "https://github.com/O/R/pulls"\n',
        r"\{pr\}",
        id="pr-url-template-without-placeholder",
    ),
    # The template becomes a link target, so a non-http(s) scheme is rejected
    # at load.
    pytest.param(
        '[tool.evaltrack]\npr_url_template = "javascript:alert(1)//{pr}"\n',
        "http",
        id="pr-url-template-with-a-non-http-scheme",
    ),
    # `tool = 5` is valid TOML, so it survives the parse. A caller catches only
    # ValueError, so an AttributeError must not escape.
    pytest.param("tool = 5\n", "'tool' must be a table", id="tool-is-not-a-table"),
    pytest.param(
        '[tool]\nevaltrack = "oops"\n', "tool.evaltrack", id="table-is-not-a-table"
    ),
    pytest.param("[tool.evaltrack]\nlocal = 123\n", "local", id="wrong-typed-key"),
    # An ignored misspelled key makes the store it names look like it lost
    # its runs.
    pytest.param("[tool.evaltrack]\nlocl = './.evaltrack'\n", "locl", id="unknown-key"),
]


@pytest.mark.parametrize(("body", "expected_message"), _BAD_CONFIGS)
def test_load_config_rejects_and_names_the_file(
    body: str, expected_message: str, tmp_path: Path
) -> None:
    _write_pyproject(tmp_path, body)
    with pytest.raises(ValueError, match=expected_message) as excinfo:
        load_config(tmp_path)
    assert str(tmp_path / "pyproject.toml") in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    ["pr_url_template", "keep_raw_results", "remte"],
    ids=["invalid-value", "removed-key", "unknown-key"],
)
def test_load_config_rejection_does_not_echo_the_value(
    key: str, tmp_path: Path
) -> None:
    """A user puts a SAS URL where a known key expects something else, or under
    a typo of a key. pydantic quotes the rejected value in `str(exc)`, which
    puts the token in a CI log."""
    _write_pyproject(
        tmp_path, f'[tool.evaltrack]\n{key} = "azure://acct/c?sig=SUPERSECRETSIG="\n'
    )
    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)
    message = str(excinfo.value)
    assert "SUPERSECRETSIG" not in message
    assert key in message, "the redacted error must still name the rejected key"


def test_the_removed_keep_raw_results_says_it_was_removed(tmp_path: Path) -> None:
    """An unknown-key error looks like a typo, so a removed key gets its own
    message."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\nkeep_raw_results = false\n")
    with pytest.raises(ValueError, match="removed in evaltrack 0.3.0"):
        load_config(tmp_path)


def test_load_config_reads_from_a_nested_directory(tmp_path: Path) -> None:
    """The table is found by searching upward, so a command run anywhere inside
    the project reads the same config as one run at its root."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\nlocal = 'azure://account/evals'\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_config(nested).local == "azure://account/evals"


def test_project_root_is_the_pyproject_directory(tmp_path: Path) -> None:
    """A relative path anchors against the project root, so the root must be
    the same from any directory inside the project."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\n")
    nested = tmp_path / "pkg" / "tests"
    nested.mkdir(parents=True)
    assert project_root(nested) == tmp_path
    assert project_root(tmp_path) == tmp_path


def test_project_root_falls_back_to_the_given_directory(tmp_path: Path) -> None:
    assert project_root(tmp_path) == tmp_path


def test_relative_path_anchors_to_the_pyproject_directory(tmp_path: Path) -> None:
    """A relative path names the same store from any working directory. Read
    against the caller's directory, it would give one project a separate store
    for every directory pytest runs from."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\nlocal = './.evaltrack'\n")
    nested = tmp_path / "tests" / "unit"
    nested.mkdir(parents=True)
    assert load_config(nested).local == f"{tmp_path}/.evaltrack"


@pytest.mark.parametrize(
    "location",
    [
        "azure://account/evals",
        "/absolute/store",
        "~/store",
    ],
)
def test_anchor_leaves_urls_and_anchored_paths_alone(
    location: str, tmp_path: Path
) -> None:
    """Only a relative path is ambiguous. Absolute and `~` paths already name
    one location, and a URL has no filesystem path to anchor."""
    assert anchor_path(location, tmp_path) == location


def test_anchor_normalizes_parent_segments(tmp_path: Path) -> None:
    base = tmp_path / "project"
    base.mkdir()
    assert anchor_path("../shared", base) == f"{tmp_path}/shared"


def test_anchor_leaves_a_url_missing_its_slashes_alone(tmp_path: Path) -> None:
    """Anchoring `azure:/account/evals` would bury the ':' mid-path, where it
    reads as an ordinary relative directory and opens as one."""
    assert anchor_path("azure:/account/evals", tmp_path) == "azure:/account/evals"


@pytest.mark.parametrize("role", ["local", "remote"])
def test_load_config_leaves_a_url_missing_its_slashes_unanchored(
    role: str, tmp_path: Path
) -> None:
    _write_pyproject(tmp_path, f'[tool.evaltrack]\n{role} = "azure:/account/evals"\n')
    assert getattr(load_config(tmp_path), role) == "azure:/account/evals"


# Each row sets the sources it names and expects the highest of them to win.
_LOCAL_PRECEDENCE = [
    pytest.param("/override", "/env", "/config", "/override", id="override-beats-both"),
    pytest.param(None, "/env", "/config", "/env", id="env-beats-config"),
    pytest.param(None, None, "/config", "/config", id="config-beats-default"),
    pytest.param("/override", None, None, "/override", id="override-only"),
    pytest.param(None, "/env", None, "/env", id="env-only"),
]


@pytest.mark.parametrize(("override", "env", "configured", "winner"), _LOCAL_PRECEDENCE)
def test_resolve_local_takes_the_highest_source_that_is_set(
    override: str | None,
    env: str | None,
    configured: str | None,
    winner: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if env is not None:
        monkeypatch.setenv(LOCAL_ENV, env)

    resolved = resolve_local(
        EvaltrackConfig(local=configured), override=override, start=tmp_path
    )

    assert resolved == winner


def test_resolve_local_falls_back_to_the_default_under_the_project(
    tmp_path: Path,
) -> None:
    """With nothing set the local repository is the project's own store, so a
    project that configures nothing still records its runs somewhere."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\n")
    nested = tmp_path / "pkg" / "tests"
    nested.mkdir(parents=True)

    assert resolve_local(EvaltrackConfig(), start=nested) == f"{tmp_path}/.evaltrack"


def test_resolve_local_uses_an_environment_value_as_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative path in the environment follows the caller's directory, like
    one typed on the command line. A configured path names the project's store
    from any directory."""
    _write_pyproject(tmp_path, "[tool.evaltrack]\nlocal = './cfg-local'\n")
    config = load_config(tmp_path)
    monkeypatch.setenv(LOCAL_ENV, "./from-env")

    assert resolve_local(config, start=tmp_path) == "./from-env"

    monkeypatch.delenv(LOCAL_ENV)
    assert resolve_local(config, start=tmp_path) == f"{tmp_path}/cfg-local"


def test_resolve_remote_prefers_the_environment_and_names_its_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source is displayed, so a push to a shared store is never silent
    about which setting sent it there."""
    monkeypatch.setenv(REMOTE_ENV, "/env")

    remote = resolve_remote(EvaltrackConfig(remote="/config"))

    assert remote is not None
    assert (remote.url, remote.source) == ("/env", "$EVALTRACK_REMOTE")


def test_resolve_remote_falls_back_to_the_config() -> None:
    remote = resolve_remote(EvaltrackConfig(remote="/config"))

    assert remote is not None
    assert (remote.url, remote.source) == ("/config", "[tool.evaltrack].remote")


def test_resolve_remote_is_none_when_nothing_names_one() -> None:
    """A remote is optional, unlike the local repository, which always resolves."""
    assert resolve_remote(EvaltrackConfig()) is None
