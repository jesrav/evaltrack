"""Run context detection from git, env, or both."""

import os
import subprocess  # nosec B404 - used only for `git rev-parse`-style metadata lookups
from collections.abc import Callable
from functools import partial
from pathlib import Path

from evaltrack.core.run_context import RunContext

_ENV_LABEL_PREFIX = "EVALTRACK_LABEL_"

# Detection must never fail a run, so any failure to launch git means no
# context. `CalledProcessError` is not an `OSError`, so it is named too.
_GIT_UNAVAILABLE = (subprocess.CalledProcessError, OSError)


def build_from_env() -> RunContext:
    """Build a `RunContext` from environment variables.

    Reads `EVALTRACK_COMMIT`, `EVALTRACK_BRANCH`, and any
    `EVALTRACK_LABEL_<KEY>` variable (lowercased after the prefix).
    """
    labels: dict[str, str] = {}
    if branch := os.environ.get("EVALTRACK_BRANCH"):
        labels["branch"] = branch
    for key, value in os.environ.items():
        if key.startswith(_ENV_LABEL_PREFIX) and value:
            labels[key[len(_ENV_LABEL_PREFIX) :].lower()] = value
    return RunContext(commit=os.environ.get("EVALTRACK_COMMIT") or None, labels=labels)


def build_from_git(
    runner: Callable[[list[str]], str | None] | None = None,
    dirty_check: Callable[[], bool | None] | None = None,
    *,
    cwd: Path | None = None,
) -> RunContext | None:
    """Build a `RunContext` from git, or None when git is unavailable or `cwd` is not a
    repo.

    `dirty_check` is separate from `runner` because empty stdout means "clean"
    here, while the `runner` contract reads empty output as a failure. `cwd`
    applies to the default runners only.
    """
    run = runner if runner is not None else partial(_default_git_runner, cwd=cwd)
    check_dirty = (
        dirty_check
        if dirty_check is not None
        else partial(_default_dirty_check, cwd=cwd)
    )
    commit = run(["rev-parse", "HEAD"])
    if not commit:
        return None
    labels: dict[str, str] = {}
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    # `--abbrev-ref` yields the literal "HEAD" on a detached HEAD.
    if branch and branch != "HEAD":
        labels["branch"] = branch
    return RunContext(commit=commit, worktree_dirty=check_dirty(), labels=labels)


def detect(
    git_runner: Callable[[list[str]], str | None] | None = None,
    dirty_check: Callable[[], bool | None] | None = None,
    *,
    cwd: Path | None = None,
) -> RunContext:
    """Build a `RunContext` from git, overlaid with `build_from_env()`.

    An env var wins over git output. In CI, git can return a temporary merge
    commit rather than the real source SHA. When the env var overrides the
    commit, git's `dirty` and `branch` are dropped rather than recorded against
    a different commit.
    """
    base = build_from_git(git_runner, dirty_check, cwd=cwd) or RunContext()
    overlay = build_from_env()
    base_labels = {} if overlay.commit else base.labels
    return RunContext(
        commit=overlay.commit or base.commit,
        worktree_dirty=None if overlay.commit else base.worktree_dirty,
        labels={**base_labels, **overlay.labels},
    )


def _default_git_runner(args: list[str], *, cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(  # nosec B603 B607: git invocation with literal args
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except _GIT_UNAVAILABLE:
        return None
    return result.stdout.strip() or None


def _default_dirty_check(*, cwd: Path | None = None) -> bool | None:
    """`--porcelain` lists untracked files, so they count as dirty."""
    try:
        result = subprocess.run(  # nosec B603 B607: git invocation with literal args
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except _GIT_UNAVAILABLE:
        return None
    return result.stdout.strip() != ""
