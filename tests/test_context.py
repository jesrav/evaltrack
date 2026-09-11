"""Run context detection from the environment, from git, and from both."""

import shutil
import subprocess
from pathlib import Path

import pytest

from evaltrack.context import build_from_env, build_from_git, detect
from evaltrack.core.run_context import RunContext

# build_from_env


def test_from_env_empty_environment() -> None:
    assert build_from_env() == RunContext()


def test_from_env_reads_commit_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVALTRACK_COMMIT", "abc123")
    monkeypatch.setenv("EVALTRACK_BRANCH", "main")
    ctx = build_from_env()
    assert ctx.commit == "abc123"
    assert ctx.labels == {"branch": "main"}


def test_from_env_extracts_label_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVALTRACK_LABEL_ENV", "staging")
    monkeypatch.setenv("EVALTRACK_LABEL_BUILD_ID", "42")
    monkeypatch.setenv("UNRELATED", "ignored")
    ctx = build_from_env()
    assert ctx.commit is None
    assert ctx.labels == {"env": "staging", "build_id": "42"}


def test_from_env_skips_empty_label_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVALTRACK_LABEL_EMPTY", "")
    assert "empty" not in build_from_env().labels


# build_from_git


def test_from_git_returns_none_when_no_commit() -> None:
    # Stands in for "git not installed" or "not a repo". The runner returns
    # None for HEAD.
    assert build_from_git(runner=lambda _args: None) is None


def test_from_git_extracts_commit_and_branch() -> None:
    responses: dict[tuple[str, ...], str] = {
        ("rev-parse", "HEAD"): "9fceb02",
        ("rev-parse", "--abbrev-ref", "HEAD"): "feature/x",
    }

    def fake_runner(args: list[str]) -> str | None:
        return responses.get(tuple(args))

    ctx = build_from_git(runner=fake_runner)
    assert ctx is not None
    assert ctx.commit == "9fceb02"
    assert ctx.labels == {"branch": "feature/x"}


@pytest.mark.parametrize("dirty", [True, False, None])
def test_from_git_records_dirty_state(dirty: bool | None) -> None:
    """The dirty check is separate from the runner that reads labels, and its
    answer reaches the context unchanged. Empty output from
    `git status --porcelain` means clean, and None means unknown."""
    ctx = build_from_git(
        runner=lambda args: "abc123" if args == ["rev-parse", "HEAD"] else None,
        dirty_check=lambda: dirty,
    )
    assert ctx is not None
    assert ctx.worktree_dirty is dirty


def test_from_git_omits_branch_label_on_detached_head() -> None:
    """`rev-parse --abbrev-ref HEAD` returns the literal "HEAD" on a detached
    head. That is not a branch name, so it is not recorded as one."""
    responses: dict[tuple[str, ...], str] = {
        ("rev-parse", "HEAD"): "9fceb02",
        ("rev-parse", "--abbrev-ref", "HEAD"): "HEAD",
    }

    ctx = build_from_git(runner=lambda args: responses.get(tuple(args)))
    assert ctx is not None
    assert ctx.labels == {}


def test_from_git_omits_missing_labels() -> None:
    """The branch can be unavailable, for example on a detached head. The
    commit alone is enough for a valid RunContext."""

    def fake_runner(args: list[str]) -> str | None:
        return "9fceb02" if args == ["rev-parse", "HEAD"] else None

    ctx = build_from_git(runner=fake_runner)
    assert ctx is not None
    assert ctx.commit == "9fceb02"
    assert ctx.labels == {}


# detect


def test_detect_env_overrides_git_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI sets `EVALTRACK_COMMIT` to the real source SHA. That must win over
    git's detached-head output."""

    def git_runner(args: list[str]) -> str | None:
        responses: dict[tuple[str, ...], str] = {
            ("rev-parse", "HEAD"): "ephemeral-merge-sha"
        }
        return responses.get(tuple(args))

    monkeypatch.setenv("EVALTRACK_COMMIT", "real-source-sha")
    ctx = detect(git_runner=git_runner)
    assert ctx.commit == "real-source-sha"


def test_detect_merges_labels_with_env_winning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def git_runner(args: list[str]) -> str | None:
        responses: dict[tuple[str, ...], str] = {
            ("rev-parse", "HEAD"): "git-sha",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
        }
        return responses.get(tuple(args))

    monkeypatch.setenv("EVALTRACK_BRANCH", "release")
    monkeypatch.setenv("EVALTRACK_LABEL_ENV", "prod")
    ctx = detect(git_runner=git_runner)
    assert ctx.commit == "git-sha"
    assert ctx.labels == {
        "branch": "release",  # env wins over git
        "env": "prod",  # env only
    }


def test_detect_drops_git_labels_when_env_overrides_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's branch label describes the local checkout. Once the environment
    overrides the SHA, the label would describe a different commit than the one
    recorded, so it is dropped like `dirty`. Labels from the environment stay."""

    def git_runner(args: list[str]) -> str | None:
        responses: dict[tuple[str, ...], str] = {
            ("rev-parse", "HEAD"): "ephemeral-merge-sha",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
        }
        return responses.get(tuple(args))

    monkeypatch.setenv("EVALTRACK_COMMIT", "real-source-sha")
    monkeypatch.setenv("EVALTRACK_LABEL_BUILD_ID", "42")
    ctx = detect(git_runner=git_runner)
    assert ctx.commit == "real-source-sha"
    assert ctx.labels == {"build_id": "42"}

    monkeypatch.setenv("EVALTRACK_BRANCH", "feature/x")
    ctx = detect(git_runner=git_runner)
    assert ctx.labels == {"branch": "feature/x", "build_id": "42"}, (
        "an env-supplied branch lands even though git's was dropped"
    )


def test_detect_keeps_git_labels_without_env_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a commit override drops git's labels. Other environment variables
    merge on top of them."""

    def git_runner(args: list[str]) -> str | None:
        responses: dict[tuple[str, ...], str] = {
            ("rev-parse", "HEAD"): "git-sha",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
        }
        return responses.get(tuple(args))

    monkeypatch.setenv("EVALTRACK_LABEL_ENV", "prod")
    ctx = detect(git_runner=git_runner)
    assert ctx.commit == "git-sha"
    assert ctx.labels == {
        "branch": "main",
        "env": "prod",
    }


def test_detect_empty_when_neither_source_has_data() -> None:
    ctx = detect(git_runner=lambda _args: None)
    assert ctx == RunContext()


def test_from_env_blank_commit_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """`EVALTRACK_COMMIT=""` counts as unset, not as a literal empty SHA."""
    monkeypatch.setenv("EVALTRACK_COMMIT", "")
    assert build_from_env().commit is None


# The tests above inject callables, so they do not catch a bad subprocess call
# or a misread of the `git status --porcelain` contract. The tests below build a
# throwaway repo and run `build_from_git()` against real git.


def _run_git(cwd: Path, *args: str) -> None:
    """Run a git command in `cwd`. A non-zero exit raises."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway repo on `main` with one committed file, `tracked.txt`.

    Sits in a subdirectory of `tmp_path`, so a sibling path stands in for one
    outside any repo. Commit signing is disabled locally: a global gitconfig
    that demands a signature, which is common in CI sandboxes, would fail
    `git commit` for a reason outside `build_from_git()`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "tracked.txt").write_text("hello\n")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-qm", "initial commit")
    return repo


def test_a_git_that_cannot_be_launched_yields_no_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `git` on PATH that cannot be executed, such as one stripped of its
    execute bit, raises `PermissionError` rather than `FileNotFoundError`.
    Detection runs where nothing catches it, so an escape costs the whole
    recorded run. A directory named `git` stands in for the unexecutable file,
    since permission bits are not enforced for every user that runs this."""
    bin_dir = tmp_path / "bin"
    (bin_dir / "git").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(tmp_path)

    assert build_from_git() is None
    assert detect() == RunContext()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_from_git_cwd_pins_the_queried_repo(git_repo: Path, tmp_path: Path) -> None:
    """`cwd=` decides which repo answers, not the working directory of the
    process.

    The recorded commit keys history across runs. A run of
    `pytest /path/to/project` started from another checkout must record the
    project's commit. The working directory here is a different git repo, so a
    wrong answer is possible.
    """
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    ctx = build_from_git(cwd=git_repo)  # process cwd is elsewhere, and another repo
    assert ctx is not None
    assert ctx.commit == expected
    assert ctx.worktree_dirty is False
    assert ctx.labels.get("branch") == "main"

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert build_from_git(cwd=outside) is None, (
        "a cwd outside any repo must not fall back to the repo containing the process cwd"
    )
    assert detect(cwd=outside).commit is None, (
        "detect must not fall back to the process cwd's repo either"
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_from_git_against_real_repo(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `git status --porcelain` call must report both a clean tree and
    a changed one."""
    monkeypatch.chdir(git_repo)

    clean = build_from_git()
    assert clean is not None
    assert clean.commit, "the real rev-parse call must yield a SHA"
    assert clean.worktree_dirty is False, "freshly-committed repo should be clean"
    assert clean.labels.get("branch") == "main"

    # An untracked file makes the tree dirty (`status --porcelain` reports
    # `?? path`).
    (git_repo / "untracked.txt").write_text("new\n")
    dirty_untracked = build_from_git()
    assert dirty_untracked is not None
    assert dirty_untracked.worktree_dirty is True

    # Commit that file, then modify a tracked file. The tree is dirty again.
    _run_git(git_repo, "add", "untracked.txt")
    _run_git(git_repo, "commit", "-qm", "add untracked")
    (git_repo / "tracked.txt").write_text("hello\nmodified\n")
    dirty_modified = build_from_git()
    assert dirty_modified is not None
    assert dirty_modified.worktree_dirty is True


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_detect_in_detached_head_ci_setup(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The usual CI shape is a detached head on a temporary commit, with
    `EVALTRACK_COMMIT` pointing at the real source SHA. The recorded context
    must not mix git's branch of "HEAD" with the commit from the environment."""
    monkeypatch.chdir(git_repo)
    _run_git(git_repo, "checkout", "-q", "--detach", "HEAD")

    plain = detect()
    assert plain.commit
    assert "branch" not in plain.labels, "a detached head alone yields no branch label"

    monkeypatch.setenv("EVALTRACK_COMMIT", "deadbeef")
    monkeypatch.setenv("EVALTRACK_BRANCH", "feature/x")
    ctx = detect()
    assert ctx.commit == "deadbeef"
    assert ctx.worktree_dirty is None, (
        "the env commit override invalidates git's dirty state"
    )
    assert ctx.labels == {"branch": "feature/x"}
