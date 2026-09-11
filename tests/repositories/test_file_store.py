"""FileStore-specific tests for opening from a path and filesystem behavior.

The shared store contract is exercised in `test_store_contract.py`,
parametrized over every backend including this one.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ulid import ULID

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import RUN_SCHEMA_VERSION, RunRecord
from evaltrack.repositories import RunRepository, open_repository
from evaltrack.repositories.file import FileStore

from ..factories import RECORDED_BY, make_eval_run


def _make_run() -> RunRecord:
    return RunRecord(
        run_schema_version=RUN_SCHEMA_VERSION,
        id=str(ULID()),
        created_at=datetime.now(UTC),
        recorded_by=RECORDED_BY,
        commit=None,
        labels={},
        tests={},
    )


# --- opening from a path ---


def test_from_path_expands_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `~` from pyproject.toml or an environment variable never met a shell,
    so `~/sub` must resolve under the home directory instead of creating a
    literal `~` directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    store = FileStore.from_path("~/sub")
    assert store.root == (tmp_path / "sub").resolve()


def test_from_path_keeps_relative_paths_relative() -> None:
    """`./.evaltrack` is the documented default form. It must keep resolving
    against the current working directory."""
    store = FileStore.from_path("./.evaltrack")
    assert store.root == (Path.cwd() / ".evaltrack").resolve()


def test_from_path_keeps_parent_segments_relative() -> None:
    store = FileStore.from_path("../shared")
    assert store.root == (Path.cwd().parent / "shared").resolve()


# --- filesystem and error behavior ---


def test_save_run_into_a_file_blocked_store_fails_loudly(tmp_path: Path) -> None:
    """When the runs directory exists as a file, save_run must propagate the
    error, not return as if the run were saved."""
    root = tmp_path / "store"
    root.mkdir()
    (root / "runs").write_bytes(b"i am a file")
    repo = RunRepository(FileStore(root))
    with pytest.raises(RepositoryUnavailableError, match="runs"):
        repo.save_run(_make_run())


def test_symlinked_runs_directory_saves_and_lists(tmp_path: Path) -> None:
    """A `runs` directory moved to a bigger disk and symlinked back resolves
    outside the store root. The key is a valid one, so the run must be saved
    through the link and listed back, not refused as escaping the root."""
    root = tmp_path / "store"
    root.mkdir()
    bigger_disk = tmp_path / "bigger-disk"
    bigger_disk.mkdir()
    (root / "runs").symlink_to(bigger_disk)
    repo = open_repository(str(root))
    run = make_eval_run()

    repo.save_run(run)

    assert (bigger_disk / f"{run.id}.json").is_file()
    assert [summary.id for summary in repo.list_runs()] == [run.id]


def test_list_empty_prefix_on_missing_root_is_empty(tmp_path: Path) -> None:
    """`list("")` on a store that is not yet created (opened but nothing
    saved) must return nothing. It must not walk the root's parent and raise a
    message that names a sibling file, which is what the empty-prefix parent
    scan once did."""
    (tmp_path / "outside-secret.txt").write_bytes(b"top secret")
    store = FileStore(tmp_path / "store")  # never created
    assert list(store.list("")) == []
    assert list(store.list("runs")) == []


def test_list_absent_namespace_does_not_walk_the_rest_of_the_root(
    tmp_path: Path,
) -> None:
    """A repository pointed at a home or working directory holds no `runs/`
    before the first run, and the rest of that directory is an unrelated tree
    the listing must not walk. Here it holds a directory whose entries cannot be
    stat'ed, which a walk fails on."""
    if os.geteuid() == 0:
        pytest.skip("root reads every directory, so nothing here is unreadable")
    root = tmp_path / "home"
    unrelated = root / "private"
    unrelated.mkdir(parents=True)
    (unrelated / "notes.txt").write_bytes(b"not a run")
    (root / "README").write_bytes(b"not a run either")
    unrelated.chmod(0o600)  # readable, so a walk lists it, but not searchable
    try:
        store = FileStore(root)
        assert list(store.list("runs/")) == []
        assert list(store.list("refs/")) == []
    finally:
        unrelated.chmod(0o700)


def test_list_runs_skips_a_non_utf8_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A filesystem holds names as bytes, so a run key can decode to a lone
    surrogate. It is not a name this repository can write, so the listing
    skips it like any other foreign key. An encode of the name for that check
    once raised a codec error out of the whole listing."""
    root = tmp_path / "store"
    repo = RunRepository(FileStore(root))
    run = _make_run()
    repo.save_run(run)
    try:
        with open(bytes(root / "runs") + b"/\xff.json", "wb") as handle:
            handle.write(b"{}")
    except OSError:
        pytest.skip("filesystem requires valid UTF-8 filenames (e.g. APFS)")

    with caplog.at_level(logging.WARNING):
        summaries = list(repo.list_runs())
    assert "cannot hold a readable run" in caplog.text, (
        "the skip must be logged, not silent"
    )

    assert [summary.id for summary in summaries] == [run.id]


def test_verify_writable_rejects_root_that_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file where a directory is needed")
    with pytest.raises(RepositoryUnavailableError, match="is not a directory"):
        FileStore(blocker).verify_writable()


def test_verify_available_rejects_root_that_is_a_file(tmp_path: Path) -> None:
    """A root that is a regular file cannot serve a single request.
    The check has to say so up front, before a caller mounts or writes to it."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file where a directory is needed")
    with pytest.raises(RepositoryUnavailableError, match="is not a directory"):
        FileStore(blocker).verify_available()


def test_verify_available_accepts_a_missing_or_existing_directory(
    tmp_path: Path,
) -> None:
    """A fresh repository does not exist until the first save, so a missing root
    is as available as an existing one."""
    FileStore(tmp_path / "missing").verify_available()
    FileStore(tmp_path).verify_available()


def test_verify_writable_rejects_root_under_a_file(tmp_path: Path) -> None:
    """The nearest existing ancestor decides. A file in the middle of the path
    means no mkdir can succeed."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file where a directory is needed")
    with pytest.raises(RepositoryUnavailableError, match="is not a directory"):
        FileStore(blocker / "repository").verify_writable()


def test_what_is_created_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    """Runs and reflogs can hold prompts and model output. SECURITY.md tells
    users to treat those as sensitive, so nothing written here must be readable
    by the group or by everyone on a shared machine. The directories carry the
    same rule: a ref name is a branch or PR name, so a listable `refs/` gives
    away what is being worked on even with every file locked down."""
    store = FileStore(tmp_path / "store")
    store.write(b"a run", "runs/01.json")
    store.append(b"an entry\n", "refs/baseline.log.jsonl")
    created = (
        store.root,
        store.root / "runs",
        store.root / "refs",
        store.root / "runs/01.json",
        store.root / "refs/baseline.log.jsonl",
    )
    for path in created:
        mode = path.stat().st_mode
        assert not mode & 0o077, f"{path.name} is {oct(mode)}"


def test_a_root_that_is_a_file_is_reported_as_unavailable_storage(
    tmp_path: Path,
) -> None:
    """A root that is a regular file means the store cannot be
    used. Every method reports that the way a failed remote call does, so one
    `except EvaltrackError` covers them all."""
    root = tmp_path / "root"
    root.write_text("a file where a directory belongs")
    repo = RunRepository(FileStore(root))
    run = _make_run()

    calls = [
        lambda: repo.verify_available(),
        lambda: repo.verify_writable(),
        lambda: repo.save_run(run),
        lambda: repo.load_run(run.id),
        lambda: list(repo.list_runs()),
        lambda: list(repo.list_refs()),
        lambda: repo.get_ref("baseline"),
        lambda: repo.move_ref("pr/1", run.id),
        lambda: repo.delete_ref_unchecked("pr/1"),
    ]
    for call in calls:
        with pytest.raises(RepositoryUnavailableError):
            call()
