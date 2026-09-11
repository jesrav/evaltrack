"""What deletes a run from a `RunRepository`, and what holds one back.

Covers the ref cascade and the refusals that protect a run some other ref
still references, or might: a reflog that cannot be read leaves the referenced
set unknown, and every delete refuses until it reads again.
"""

import pytest
from ulid import ULID

from evaltrack.core.errors import (
    BaselineRefProtectedError,
    CorruptRecordError,
    InvalidIdentifierError,
    RefNotFoundError,
    RunReferencedError,
)
from evaltrack.repositories import (
    RunRepository,
    delete_ref_and_orphaned_runs,
    delete_run_if_unreferenced,
    promote,
)
from evaltrack.repositories.store import ObjectStore

from .helpers import RepositoryFactory, StoreFactory, make_run

# --- RunRepository: retention (delete_run, delete_ref_and_orphaned_runs) ---


def test_delete_run_removes_and_returns_id(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    assert repo.delete_run_unchecked(run.id) == run.id
    assert repo.load_run(run.id) is None


def test_delete_run_absent_returns_none(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    assert repo.delete_run_unchecked(str(ULID())) is None


def test_delete_ref_and_orphaned_runs_cascades_to_scoped_runs(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    a, b = make_run(), make_run()
    repo.save_run(a)
    repo.save_run(b)
    repo.move_ref("pr/42", a.id)
    repo.move_ref("pr/42", b.id)  # supersede a: reflog has a+b, pointer = b

    result = delete_ref_and_orphaned_runs(repo, "pr/42")

    assert set(result.deleted_runs) == {a.id, b.id}
    assert repo.get_ref("pr/42") is None
    assert list(repo.get_reflog("pr/42")) == []
    assert repo.load_run(a.id) is None
    assert repo.load_run(b.id) is None


def test_delete_ref_and_orphaned_runs_keeps_runs_reachable_elsewhere(
    repository_factory: RepositoryFactory,
) -> None:
    """A run also referenced by baseline must survive removal of a pr/ ref."""
    repo = repository_factory()
    shared = make_run()
    repo.save_run(shared)
    repo.move_ref("baseline", shared.id)
    repo.move_ref("pr/9", shared.id)  # pr/9's reflog references shared too

    result = delete_ref_and_orphaned_runs(repo, "pr/9")

    assert result.deleted_runs == [], "baseline still references the run"
    assert repo.load_run(shared.id) == shared
    assert repo.get_ref("pr/9") is None, "the ref itself goes even though its run stays"


def test_delete_ref_and_orphaned_runs_cascades_past_superseded_runs(
    repository_factory: RepositoryFactory,
) -> None:
    """Removal drops the ref's whole history from the keep-set, so it reclaims
    the superseded runs too. A run another ref's history also reached stays."""
    repo = repository_factory()
    superseded, tip, shared, other = make_run(), make_run(), make_run(), make_run()
    for run in (superseded, tip, shared, other):
        repo.save_run(run)
    repo.move_ref("pr/1", superseded.id)
    repo.move_ref("pr/1", shared.id)
    repo.move_ref("pr/1", tip.id)  # pr/1 history: superseded, shared, tip
    repo.move_ref("pr/2", shared.id)
    repo.move_ref("pr/2", other.id)  # pr/2 history: shared, other

    result = delete_ref_and_orphaned_runs(repo, "pr/1")

    assert set(result.deleted_runs) == {superseded.id, tip.id}, (
        "the runs only pr/1's history reached"
    )
    assert repo.load_run(superseded.id) is None
    assert repo.load_run(tip.id) is None
    assert repo.load_run(shared.id) == shared, "pr/2's history still reaches it"


def test_delete_ref_and_orphaned_runs_missing_ref_raises(
    repository_factory: RepositoryFactory,
) -> None:
    """A typo'd ref name must fail loudly. An empty RefDeletion would make a
    delete that touched nothing look like a success."""
    repo = repository_factory()
    with pytest.raises(RefNotFoundError, match="pr/999"):
        delete_ref_and_orphaned_runs(repo, "pr/999")


def test_delete_ref_and_orphaned_runs_deletes_zombie_ref_with_empty_reflog(
    store_factory: StoreFactory,
) -> None:
    """A failed first append leaves a zero-byte reflog object. A crash between
    the create and the append causes it, as does a full disk.

    A ref exists when its reflog object exists, so this one is listed. It has
    no tip and no runs of its own, so `delete_ref_and_orphaned_runs` must delete
    it rather than raise. Otherwise nothing can ever clean it up.
    """
    store = store_factory()
    repo = RunRepository(store)
    # `write` plants the empty object directly. On Azure this makes a block
    # blob where a torn append leaves an append blob. Listing and deletion,
    # which this test asserts, treat both alike.
    store.write(b"", "refs/pr/13.log.jsonl")

    assert list(repo.list_refs()) == ["pr/13"], (
        "a ref exists once its reflog object does"
    )
    result = delete_ref_and_orphaned_runs(repo, "pr/13")
    assert result.deleted_runs == [], "an empty reflog references no runs"
    assert list(repo.list_refs()) == []


def _truncate_reflog(store: ObjectStore, path: str, content: bytes) -> None:
    """Replace a reflog with an unparsable partial line.

    Delete first rather than `write` over it, per the `ObjectStore`
    rule that a key belongs to the mode that created it, and `move_ref` creates
    a reflog through `append`. `test_store_contract.py` pins that the delete
    makes this portable.
    """
    store.delete(path)
    store.write(content, path)


def test_a_reflog_torn_inside_a_character_holds_retention_back(
    store_factory: StoreFactory,
) -> None:
    """Reflog lines are UTF-8 and a PR title holds any character, so a crash
    mid-append can cut a line inside one character. Those bytes fail to decode,
    not to parse. Retention has one answer for a history it cannot read, and
    that answer does not depend on how the line broke."""
    store = store_factory()
    repo = RunRepository(store)
    referenced, stray = make_run(), make_run()
    repo.save_run(referenced)
    repo.save_run(stray)
    repo.move_ref("pr/1", referenced.id, title="fix café")
    store.append(
        '{"run_id": "01KXB8", "title": "fix café'.encode()[:-1],
        "refs/pr/1.log.jsonl",
    )

    with pytest.raises(CorruptRecordError, match="pr/1"):
        delete_run_if_unreferenced(repo, stray.id)
    assert repo.load_run(stray.id) is not None


def test_a_torn_line_below_a_readable_tip_holds_retention_back(
    store_factory: StoreFactory,
) -> None:
    """A ref's whole history is a root, so a line torn anywhere in it leaves
    the referenced set unknown, and not only a line at the end. A scan of the
    tips alone would let the runs the older entries hold go."""
    store = store_factory()
    repo = RunRepository(store)
    superseded, tip = make_run(), make_run()
    repo.save_run(superseded)
    repo.save_run(tip)
    repo.move_ref("pr/5", superseded.id)
    repo.move_ref("pr/5", tip.id)
    tip_line = store.read("refs/pr/5.log.jsonl").splitlines()[-1]
    _truncate_reflog(store, "refs/pr/5.log.jsonl", b'{"run_id": "01BX5\n' + tip_line)

    with pytest.raises(CorruptRecordError, match="pr/5"):
        delete_run_if_unreferenced(repo, superseded.id)
    assert repo.load_run(superseded.id) is not None, (
        "pinned only by an entry below the tear"
    )


def test_delete_run_if_unreferenced_refuses_over_an_unrelated_ref_with_unknown_pins(
    store_factory: StoreFactory,
) -> None:
    """The unknown set can be any run, so more than the corrupt ref's own runs
    are at risk. Nothing goes until the history reads again."""
    store = store_factory()
    repo = RunRepository(store)
    unreferenced, other = make_run(), make_run()
    repo.save_run(unreferenced)
    repo.save_run(other)
    repo.move_ref("pr/1", other.id)
    store.append(b'{"run_id": "01KXB8', "refs/pr/1.log.jsonl")

    with pytest.raises(CorruptRecordError, match="pr/1"):
        delete_run_if_unreferenced(repo, unreferenced.id)
    assert repo.load_run(unreferenced.id) is not None, (
        "the refusal protects runs beyond the corrupt ref's own"
    )


def test_delete_ref_and_orphaned_runs_refuses_when_another_refs_history_is_unreadable(
    store_factory: StoreFactory,
) -> None:
    """The cascade needs to know what every other ref references. With the
    baseline history unreadable, no run can be shown to be unreachable, so the
    whole removal refuses, the ref included. A ref left behind can be removed
    later. A deleted promoted run cannot be brought back."""
    store = store_factory()
    repo = RunRepository(store)
    scoped, promoted = make_run(), make_run()
    repo.save_run(scoped)
    repo.save_run(promoted)
    repo.move_ref("pr/2", scoped.id)
    repo.move_ref("baseline", promoted.id)
    store.append(b'{"run_id": "01KXB8', "refs/baseline.log.jsonl")

    with pytest.raises(CorruptRecordError, match="baseline"):
        delete_ref_and_orphaned_runs(repo, "pr/2")

    assert sorted(repo.list_refs()) == ["baseline", "pr/2"], (
        "the refused removal leaves the ref in place"
    )
    assert repo.load_run(scoped.id) is not None, "the refused removal deletes nothing"
    assert repo.load_run(promoted.id) is not None


def test_delete_run_if_unreferenced_refuses_when_pins_are_unknown(
    store_factory: StoreFactory,
) -> None:
    """The guard stops a delete that would strand a ref. An unreadable reflog
    makes the answer unknown, and unknown must not be read as unreferenced.
    That reading loses a run for good."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    repo.move_ref("keep-me", run.id)
    _truncate_reflog(store, "refs/keep-me.log.jsonl", b'{"run_id": "01BX5')

    with pytest.raises(CorruptRecordError, match="keep-me"):
        delete_run_if_unreferenced(repo, run.id)
    assert repo.load_run(run.id) is not None


def test_delete_ref_and_orphaned_runs_refuses_over_a_foreign_ref_key(
    store_factory: StoreFactory,
) -> None:
    """A foreign ref key makes reachability unknown exactly like an unreadable
    reflog does, so it gets the same outcome: nothing is deleted, and the error
    names the key to move out of the way."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/3", run.id)
    store.write(b"", "refs/notes:2024.log.jsonl")

    with pytest.raises(InvalidIdentifierError, match="notes:2024"):
        delete_ref_and_orphaned_runs(repo, "pr/3")

    assert repo.get_ref("pr/3") is not None, "the refused removal leaves the ref"
    assert repo.load_run(run.id) is not None, "the refused removal deletes nothing"
    # As an argument the key is refused outright: nothing can be stored under
    # it, so there is no such ref to remove.
    with pytest.raises(InvalidIdentifierError):
        delete_ref_and_orphaned_runs(repo, "notes:2024")


def test_delete_run_if_unreferenced_refuses_over_a_foreign_ref_key(
    store_factory: StoreFactory,
) -> None:
    """The guard traces every stored ref name, a key it cannot read a log for
    included. That key can be the run's only reference, and unknown must not be
    read as unreferenced."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    store.write(b"", "refs/notes:2024.log.jsonl")

    with pytest.raises(InvalidIdentifierError, match="notes:2024"):
        delete_run_if_unreferenced(repo, run.id)
    assert repo.load_run(run.id) is not None


def test_delete_ref_and_orphaned_runs_refuses_baseline(
    repository_factory: RepositoryFactory,
) -> None:
    """The reserved `baseline` ref is refused. Its removal would cascade-delete
    the whole promoted history, which retention keeps in full."""
    repo = repository_factory()
    old, new = make_run(), make_run()
    repo.save_run(old)
    repo.save_run(new)
    repo.move_ref("baseline", old.id)
    repo.move_ref("baseline", new.id)  # history: old (reflog) + new (tip)

    with pytest.raises(BaselineRefProtectedError):
        delete_ref_and_orphaned_runs(repo, "baseline")

    ref = repo.get_ref("baseline")
    assert ref is not None and ref.run_id == new.id, (
        "the refused removal leaves the tip in place"
    )
    assert [e.run_id for e in repo.get_reflog("baseline")] == [old.id, new.id]
    assert repo.load_run(old.id) == old
    assert repo.load_run(new.id) == new


def test_delete_run_if_unreferenced_deletes_unpinned(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    assert delete_run_if_unreferenced(repo, run.id) == run.id
    assert repo.load_run(run.id) is None


def test_delete_run_if_unreferenced_absent_returns_none(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    assert delete_run_if_unreferenced(repo, str(ULID())) is None


def test_delete_run_if_unreferenced_refuses_ref_tip(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.move_ref("pr/42", run.id)
    with pytest.raises(RunReferencedError) as exc:
        delete_run_if_unreferenced(repo, run.id)
    assert exc.value.refs == ["pr/42"]
    assert repo.load_run(run.id) == run, "the refused delete leaves the run untouched"


def test_delete_run_if_unreferenced_refuses_baseline_history(
    repository_factory: RepositoryFactory,
) -> None:
    """A superseded baseline (in the reflog but not the current tip) is still
    referenced by the baseline history and cannot be deleted on its own."""
    repo = repository_factory()
    first, second = make_run(), make_run()
    repo.save_run(first)
    repo.save_run(second)
    repo.move_ref("baseline", first.id)
    repo.move_ref("baseline", second.id)  # first is now history, not the tip
    with pytest.raises(RunReferencedError) as exc:
        delete_run_if_unreferenced(repo, first.id)
    assert exc.value.refs == ["baseline"]


def test_delete_run_if_unreferenced_refuses_a_superseded_pr_run(
    repository_factory: RepositoryFactory,
) -> None:
    """A pr/ reflog is a root, so a superseded PR run is referenced by the ref
    that once pointed at it and cannot be deleted on its own."""
    repo = repository_factory()
    superseded, tip = make_run(), make_run()
    repo.save_run(superseded)
    repo.save_run(tip)
    repo.move_ref("pr/7", superseded.id)
    repo.move_ref("pr/7", tip.id)
    with pytest.raises(RunReferencedError) as exc:
        delete_run_if_unreferenced(repo, superseded.id)
    assert exc.value.refs == ["pr/7"]
    assert repo.load_run(superseded.id) == superseded


def test_promote_cleanup_removes_source_and_superseded(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    a, b = make_run(), make_run()
    repo.save_run(a)
    repo.save_run(b)
    repo.move_ref("pr/42", a.id)
    repo.move_ref("pr/42", b.id)  # tip = b

    result = promote(repo, "pr/42", commit="merge-sha", cleanup=True)

    assert result.baseline.run_id == b.id
    assert result.cleanup is not None
    assert result.cleanup.deleted_runs == [a.id]
    assert repo.get_ref("pr/42") is None
    assert repo.load_run(b.id) == b, "the promoted run survives via baseline"
    assert repo.load_run(a.id) is None, "the superseded run is reclaimed"
    [entry] = list(repo.get_reflog("baseline"))
    assert entry.run_id == b.id
    assert entry.commit == "merge-sha"


def test_promote_cleanup_refuses_after_the_promote_when_a_reflog_is_unreadable(
    store_factory: StoreFactory,
) -> None:
    """Baseline moves before the cleanup, so the promote lands even though the
    cleanup refuses. The source ref and every run stay, and a retry once the
    reflog reads again finishes the job."""
    store = store_factory()
    repo = RunRepository(store)
    superseded, tip, other = make_run(), make_run(), make_run()
    for run in (superseded, tip, other):
        repo.save_run(run)
    repo.move_ref("pr/2", superseded.id)
    repo.move_ref("pr/2", tip.id)
    repo.move_ref("keep", other.id)
    store.append(b'{"run_id": "01KXB8', "refs/keep.log.jsonl")

    with pytest.raises(CorruptRecordError, match="keep"):
        promote(repo, "pr/2", cleanup=True)

    baseline = repo.get_ref("baseline")
    assert baseline is not None and baseline.run_id == tip.id, "the promote landed"
    assert repo.get_ref("pr/2") is not None, "the refused cleanup leaves the ref"
    assert repo.load_run(superseded.id) is not None, (
        "the refused cleanup deletes nothing"
    )
