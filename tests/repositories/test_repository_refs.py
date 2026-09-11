"""Refs in a `RunRepository`.

Covers reflogs, promotion, and the name rules that keep a ref usable as a
storage key.
"""

from dataclasses import dataclass

import pytest

from evaltrack.core.errors import (
    CorruptRecordError,
    InvalidIdentifierError,
    RefNotFoundError,
)
from evaltrack.core.run_record import (
    RunRecord,
)
from evaltrack.repositories import RunRepository, delete_ref_and_orphaned_runs, promote
from evaltrack.repositories.store import ObjectStore

from ..fakes import MemoryStore
from .helpers import RepositoryFactory, StoreFactory, make_run, race

# --- RunRepository: refs ---


def test_get_missing_ref_returns_none(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    assert repo.get_ref("baseline") is None


def test_get_reflog_missing_returns_empty(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    assert list(repo.get_reflog("baseline")) == []


def test_move_ref_sets_the_ref_target(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    ref = repo.move_ref("pr/123", run.id)
    fetched = repo.get_ref("pr/123")
    assert fetched is not None
    assert fetched.run_id == run.id
    assert fetched == ref


def test_move_ref_appends_reflog_entry(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/123", run.id, commit="abc", pr=123)
    entries = list(repo.get_reflog("pr/123"))
    assert len(entries) == 1
    assert entries[0].run_id == run.id
    assert entries[0].commit == "abc"
    assert entries[0].pr == 123


def test_move_ref_round_trips_title(repository_factory: RepositoryFactory) -> None:
    """The PR title is ref metadata written onto the reflog entry, so it survives
    a store/read round-trip like the PR number does."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/123", run.id, pr=123, title="Add caching")
    [entry] = list(repo.get_reflog("pr/123"))
    assert entry.pr == 123
    assert entry.title == "Add caching"


# Names `_ensure_safe_ref_name` must reject. Its docstring carries the rationale.
_UNSAFE_NAMES = [
    "..",
    ".",
    "../secret",
    "../../etc/passwd",
    "/abs",
    "a/../b",
    "a/./b",
    "a//b",
    "back\\slash",
    "c:/evil",
    "a:b",
    "nul\x00byte",
    "carriage\rreturn",
    "line\nfeed",
    'double"quote',
    "del\x7fchar",
    "esc\x1bape",
    # A lone surrogate: what a non-UTF-8 argv or store key decodes to. Storage
    # keys are bytes, so a name with no encoding is a naming error like the rest,
    # not a codec error escaping from wherever the name is first encoded.
    "\udcff",
    "",
    # Legal in a git branch name, but not here: each is a metacharacter in a
    # URL path segment, and a ref name is routed as one.
    "user@corp",
    "v1.2+build",
    "issue#42",
    "100%done",
    "with space",
    # Non-ASCII. Two lowercase names can otherwise fold together, `ß` and
    # `ss` for example.
    "caf\u00e9",
]


# Name validation is a pure check on the name, run before any store call, so
# these tests take a plain in-memory repository rather than `repository_factory`.
# A run per backend doubles the test count, and the network round-trips against
# live Azure, over the same code path.
def _make_memory_repository() -> RunRepository:
    return RunRepository(MemoryStore())


@pytest.mark.parametrize("name", _UNSAFE_NAMES)
def test_unsafe_ref_name_rejected(name: str) -> None:
    repo = _make_memory_repository()
    run = make_run()
    repo.save_run(run)
    with pytest.raises(InvalidIdentifierError):
        repo.get_ref(name)
    with pytest.raises(InvalidIdentifierError):
        list(repo.get_reflog(name))
    with pytest.raises(InvalidIdentifierError):
        repo.move_ref(name, run.id)
    with pytest.raises(InvalidIdentifierError):
        repo.delete_ref_unchecked(name)


@pytest.mark.parametrize("run_id", [*_UNSAFE_NAMES, "r" * 201, "not-a-ulid"])
def test_a_run_id_that_is_not_a_ulid_is_rejected(run_id: str) -> None:
    """A run id is normally generated, but a lookup takes one from the caller,
    so it can be malformed. The ULID rule covers everything the ref name rules
    cover, because none of these is 26 Crockford characters."""
    with pytest.raises(InvalidIdentifierError):
        _make_memory_repository().load_run(run_id)


@pytest.mark.parametrize("name", ["Release", "BASELINE", "pr/Feature-1"])
def test_non_lowercase_ref_name_rejected(name: str) -> None:
    """The rule holds on every path that names a ref, so a ref is never written
    under one case and looked for under another."""
    repo = _make_memory_repository()
    run = make_run()
    repo.save_run(run)
    with pytest.raises(InvalidIdentifierError):
        repo.validate_ref_name(name)
    with pytest.raises(InvalidIdentifierError):
        repo.get_ref(name)
    with pytest.raises(InvalidIdentifierError):
        list(repo.get_reflog(name))
    with pytest.raises(InvalidIdentifierError):
        repo.move_ref(name, run.id)
    with pytest.raises(InvalidIdentifierError):
        repo.delete_ref_unchecked(name)
    with pytest.raises(InvalidIdentifierError):
        delete_ref_and_orphaned_runs(repo, name)


def test_validate_ref_name_accepts_and_message_explains_rules() -> None:
    """A reader sees this message directly, so it must state the naming rules
    rather than only repeat the rejected value."""
    repo = _make_memory_repository()
    assert repo.validate_ref_name("pr/123") == "pr/123"
    with pytest.raises(InvalidIdentifierError) as excinfo:
        repo.validate_ref_name("../../evil")
    message = str(excinfo.value)
    assert "../../evil" in message
    assert "lowercase letters, digits" in message, "what a name may hold"
    assert "pr/123" in message, "an example of a valid name"


# Over the cap as one segment, as the first of several, and spread across
# segments that are each short.
_OVERSIZED_NAMES = [
    "x" * 201,
    "x" * 201 + "/tip",
    "/".join(["x" * 100] * 3),
]


@pytest.mark.parametrize("name", _OVERSIZED_NAMES)
def test_oversized_ref_name_rejected(name: str) -> None:
    """The run body is saved before the ref is written, so a name that only fails
    at the store leaves the run behind with nothing pointing at it."""
    repo = _make_memory_repository()
    run = make_run()
    repo.save_run(run)
    with pytest.raises(InvalidIdentifierError) as excinfo:
        repo.validate_ref_name(name)
    assert "200 characters" in str(excinfo.value), "the error must name the cap"
    with pytest.raises(InvalidIdentifierError):
        repo.move_ref(name, run.id)


def test_ref_name_at_the_limit_round_trips(
    repository_factory: RepositoryFactory,
) -> None:
    """The cap must be low enough that every backend stores the key it builds, so
    this writes the ref rather than only re-checking the validator's arithmetic."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    name = "x" * 100 + "/" + "x" * 99
    assert len(name.encode()) == 200, "the name under test must sit exactly at the cap"
    assert repo.validate_ref_name(name) == name
    repo.move_ref(name, run.id)
    fetched = repo.get_ref(name)
    assert fetched is not None
    assert fetched.run_id == run.id


def test_ref_name_segment_at_the_limit_allowed(
    repository_factory: RepositoryFactory,
) -> None:
    """The cap is inclusive, so a name right at it must still round-trip."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    name = "x" * 200
    assert repo.validate_ref_name(name) == name
    repo.move_ref(name, run.id)
    assert repo.get_ref(name) is not None


def test_namespaced_ref_name_allowed(repository_factory: RepositoryFactory) -> None:
    """A forward slash is legitimate for namespaced refs like `pr/123`. Only
    traversal-significant segments are rejected."""

    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/123", run.id)
    assert repo.get_ref("pr/123") is not None


def test_move_ref_repeated_appends_entries_in_order(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    r1, r2, r3 = make_run(), make_run(), make_run()
    for r in (r1, r2, r3):
        repo.save_run(r)
    repo.move_ref("baseline", r1.id, commit="c1", pr=1)
    repo.move_ref("baseline", r2.id, commit="c2", pr=2)
    repo.move_ref("baseline", r3.id, commit="c3", pr=3)
    entries = list(repo.get_reflog("baseline"))
    assert [e.commit for e in entries] == ["c1", "c2", "c3"]
    pointer = repo.get_ref("baseline")
    assert pointer is not None
    assert pointer.run_id == r3.id


def test_concurrent_move_ref_to_a_fresh_ref_loses_neither_entry(
    repository_factory: RepositoryFactory,
) -> None:
    """Two concurrent `move_ref` calls on the same ref both append. The current
    target is the last entry, and neither write is lost. A fresh ref runs the
    race that creates the reflog."""
    repo = repository_factory()
    r1, r2 = make_run(), make_run()
    repo.save_run(r1)
    repo.save_run(r2)
    race(
        lambda: repo.move_ref("pr/racy", r1.id),
        lambda: repo.move_ref("pr/racy", r2.id),
    )
    entries = list(repo.get_reflog("pr/racy"))
    assert len(entries) == 2, "neither concurrent append may be lost"
    assert {e.run_id for e in entries} == {r1.id, r2.id}
    tip = repo.get_ref("pr/racy")
    assert tip is not None
    assert tip.run_id == entries[-1].run_id, (
        "the pointer must agree with the last entry, whichever write won"
    )


def test_move_ref_to_current_target_is_idempotent(
    repository_factory: RepositoryFactory,
) -> None:
    """A move to the run a ref already points at appends no duplicate entry, so
    a retried push or promote is a no-op."""
    repo = repository_factory()
    r1, r2 = make_run(), make_run()
    repo.save_run(r1)
    repo.save_run(r2)
    repo.move_ref("baseline", r1.id)
    repo.move_ref("baseline", r1.id)  # same target: no-op
    repo.move_ref("baseline", r2.id)  # different target: appends
    repo.move_ref("baseline", r2.id)  # same target again: no-op
    entries = list(repo.get_reflog("baseline"))
    assert [e.run_id for e in entries] == [r1.id, r2.id], (
        "the same-target moves must append nothing"
    )
    current = repo.get_ref("baseline")
    assert current is not None
    assert current.run_id == r2.id


def test_tail_reflog_returns_last_n_newest_first(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    runs = [make_run() for _ in range(5)]
    for r in runs:
        repo.save_run(r)
        repo.move_ref("baseline", r.id)
    tail = repo.tail_reflog("baseline", 3)
    assert [e.run_id for e in tail] == [runs[4].id, runs[3].id, runs[2].id]


def test_tail_reflog_more_than_history_returns_all(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    r1, r2 = make_run(), make_run()
    repo.save_run(r1)
    repo.save_run(r2)
    repo.move_ref("x", r1.id)
    repo.move_ref("x", r2.id)
    assert [e.run_id for e in repo.tail_reflog("x", 100)] == [r2.id, r1.id]


def test_tail_reflog_missing_ref_is_empty(
    repository_factory: RepositoryFactory,
) -> None:
    assert repository_factory().tail_reflog("nope", 10) == []


def test_torn_reflog_line_raises_with_ref_file_line_and_hint(
    store_factory: StoreFactory,
) -> None:
    """A crash during an append leaves a torn trailing line.

    The error must give the reader everything a repair by hand needs: the ref,
    the storage key, the line number, and the fix. It must never skip the line,
    because that hides real corruption.
    """
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/1", run.id)
    store.append(b'{"run_id": "01KXB8', "refs/pr/1.log.jsonl")

    with pytest.raises(CorruptRecordError) as excinfo:
        repo.get_ref("pr/1")
    message = str(excinfo.value)
    assert "pr/1" in message
    assert "refs/pr/1.log.jsonl" in message
    assert "line 2" in message
    assert "docs/repositories.md#repairing-a-torn-reflog" in message, (
        "the recovery hint"
    )

    with pytest.raises(CorruptRecordError, match="line 2"):
        list(repo.get_reflog("pr/1"))


# Trailing reflog lines a store can end up holding: a crash mid-append, a
# hand-edit, or anything else that wrote bytes the entry model rejects. Entries
# are UTF-8 and a PR title holds any character, so a tear can cut a line inside
# one character. Those bytes do not decode.
_CORRUPT_REFLOG_LINES = [
    pytest.param(b'{"run_id": "01KXB8', id="torn"),
    pytest.param(
        '{"run_id": "01KXB8", "title": "fix café'.encode()[:-1], id="torn-utf8"
    ),
    pytest.param(b'{"nope": 1}\n', id="schema-invalid"),
    pytest.param(b"5\n", id="not-an-object"),
]


@dataclass(frozen=True)
class _CorruptReflogRepo:
    """A repository whose `pr/1` ref has one good entry and an unreadable
    line 2. `run` is the run the good entry points at."""

    repo: RunRepository
    run: RunRecord


def _make_repo_with_corrupt_reflog(
    store: ObjectStore, corrupt_line: bytes
) -> _CorruptReflogRepo:
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/1", run.id)
    store.append(corrupt_line, "refs/pr/1.log.jsonl")
    return _CorruptReflogRepo(repo, run)


@pytest.mark.parametrize("corrupt_line", _CORRUPT_REFLOG_LINES)
def test_specific_reflog_reads_name_the_corrupt_line(
    store_factory: StoreFactory, corrupt_line: bytes
) -> None:
    """Every read of this one ref must fail and name the line to fix, whatever
    is wrong with it. A line that broke the schema once raised an error with no
    line number in it, which no reader can act on."""
    repo = _make_repo_with_corrupt_reflog(store_factory(), corrupt_line).repo
    for read in (
        lambda: repo.get_ref("pr/1"),
        lambda: list(repo.get_reflog("pr/1")),
        lambda: repo.tail_reflog("pr/1", 5),
    ):
        with pytest.raises(CorruptRecordError) as excinfo:
            read()
        message = str(excinfo.value)
        assert "pr/1" in message
        assert "refs/pr/1.log.jsonl" in message
        assert "line 2" in message, "the exact line a repair needs"
        assert "docs/repositories.md#repairing-a-torn-reflog" in message, (
            "the recovery hint"
        )


@pytest.mark.parametrize("corrupt_line", _CORRUPT_REFLOG_LINES)
def test_delete_ref_and_orphaned_runs_refuses_a_ref_whose_reflog_is_corrupt(
    store_factory: StoreFactory, corrupt_line: bytes
) -> None:
    """The ref's own history references an unknown set, so the removal refuses
    with the same line-naming error every other read gives. Repairing the
    reflog is the fix, and the unguarded `delete_ref_unchecked` remains for a ref not
    worth repairing."""
    corrupted = _make_repo_with_corrupt_reflog(store_factory(), corrupt_line)
    with pytest.raises(CorruptRecordError, match="line 2"):
        delete_ref_and_orphaned_runs(corrupted.repo, "pr/1")
    assert list(corrupted.repo.list_refs()) == ["pr/1"], (
        "the refused removal leaves the ref in place"
    )
    assert corrupted.repo.load_run(corrupted.run.id) is not None, (
        "the refused removal deletes nothing"
    )


def test_delete_ref_reads_no_history(store_factory: StoreFactory) -> None:
    """The unguarded delete needs the ref to exist, nothing more, so it stays
    available however unreadable the history is."""
    repo = _make_repo_with_corrupt_reflog(store_factory(), b'{"run_id": "01KXB8').repo
    repo.delete_ref_unchecked("pr/1")
    assert list(repo.list_refs()) == []


def test_delete_ref_removes_reflog(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/9", run.id, commit="abc")
    repo.delete_ref_unchecked("pr/9")
    assert repo.get_ref("pr/9") is None
    assert list(repo.get_reflog("pr/9")) == []


def test_delete_ref_missing_is_noop(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    # The absence of an exception is the assertion.
    repo.delete_ref_unchecked("never-existed")


def test_list_refs_yields_names_not_storage_keys(
    repository_factory: RepositoryFactory,
) -> None:
    """A ref is stored as a reflog file, so the listing must strip the suffix
    rather than hand back the key it found."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("baseline", run.id)
    repo.move_ref("pr/1", run.id)
    repo.move_ref("pr/2", run.id)
    assert sorted(repo.list_refs()) == ["baseline", "pr/1", "pr/2"]


def test_promote_carries_the_source_refs_stored_pr(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/42", run.id, commit="pr-tip-sha", pr=42)
    result = promote(repo, "pr/42", commit="merge-sha")
    assert result.baseline.run_id == run.id
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.commit == "merge-sha", "promote's own commit, not the source ref's"
    assert entry.pr == 42, "carried from the source ref, not parsed from its name"


def test_promote_carries_the_source_refs_title(
    repository_factory: RepositoryFactory,
) -> None:
    """A promoted baseline shows which PR (and its title) it came from, carried
    from the source ref's tip entry alongside the PR number."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/42", run.id, pr=42, title="Add caching")
    promote(repo, "pr/42", commit="merge-sha")
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.pr == 42
    assert entry.title == "Add caching"


def test_promote_carries_pr_from_a_freely_named_ref(
    repository_factory: RepositoryFactory,
) -> None:
    """The PR number is stored metadata, not parsed from the ref name, so a ref
    named anything still promotes with its PR number intact."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("alice/login-fix", run.id, pr=42)
    promote(repo, "alice/login-fix")
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.pr == 42


def test_promote_without_a_stored_pr_records_no_pr(
    repository_factory: RepositoryFactory,
) -> None:
    """A `pr/`-named ref with no stored PR number promotes with pr=None. The
    name is not parsed."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/42", run.id)  # no pr recorded
    promote(repo, "pr/42")
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.pr is None


def test_promote_missing_source_raises(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    with pytest.raises(RefNotFoundError, match="pr/999"):
        promote(repo, "pr/999", commit="m")
