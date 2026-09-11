"""What happens to a run after it is recorded. A run is promoted, and later
collected when no ref reaches it any more.

Each operation here is built from the repository's own reads and writes, and
touches no storage of its own.
"""

from pydantic import BaseModel

from evaltrack.core.errors import (
    BaselineRefProtectedError,
    RefNotFoundError,
    RunReferencedError,
)
from evaltrack.core.refs import BASELINE_REF, ReflogEntry
from evaltrack.core.run_record import ensure_run_id
from evaltrack.repositories.repository import RunRepository


class RefDeletion(BaseModel):
    """Outcome of `delete_ref_and_orphaned_runs`.

    Attributes:
        ref: The ref that was deleted.
        deleted_runs: Runs only the removed ref reached.
    """

    ref: str
    deleted_runs: list[str] = []


class PromoteResult(BaseModel):
    """Outcome of `promote`. `cleanup` is None unless `cleanup=True`."""

    baseline: ReflogEntry
    cleanup: RefDeletion | None = None


def promote(
    repo: RunRepository, ref: str, *, commit: str | None = None, cleanup: bool = False
) -> PromoteResult:
    """Move `baseline` onto the target of `ref`, then with `cleanup=True`
    `delete_ref_and_orphaned_runs(repo, ref)`. Baseline moves first, so the
    promoted run survives.

    Raises:
        RefNotFoundError: when `ref` does not exist.
        BaselineRefProtectedError: when `ref` is the baseline and `cleanup`
            is set.
        InvalidIdentifierError: when no ref can be stored under `ref`, or
            `cleanup` is set and a foreign key sits under the refs prefix.
        CorruptRecordError: when `cleanup` is set and a reflog does not
            parse. Baseline has moved, and nothing was deleted.
    """
    entry = repo.get_ref(ref)
    if entry is None:
        raise RefNotFoundError(ref)
    # The PR comes from the source entry, not the ref name, because a ref
    # can carry any name.
    baseline = repo.move_ref(
        BASELINE_REF, entry.run_id, commit=commit, pr=entry.pr, title=entry.title
    )
    return PromoteResult(
        baseline=baseline,
        cleanup=delete_ref_and_orphaned_runs(repo, ref) if cleanup else None,
    )


def delete_ref_and_orphaned_runs(repo: RunRepository, name: str) -> RefDeletion:
    """Drop a ref and delete the runs reachable only through it. Immediate,
    and not atomic under concurrent writers.

    Nothing is deleted unless every reflog reads, this one included, since
    an unread history leaves the referenced set unknown, not empty.

    Raises:
        RefNotFoundError: when no such ref exists.
        BaselineRefProtectedError: for the reserved `baseline` ref.
        InvalidIdentifierError: when no ref can be stored under `name`, or a
            foreign key sits under the refs prefix.
        CorruptRecordError: when a reflog does not parse.
    """
    # Before the baseline guard, so the comparison is against a lowercase name.
    repo.validate_ref_name(name)
    if name == BASELINE_REF:
        raise BaselineRefProtectedError(
            f"refusing to remove the reserved {BASELINE_REF!r} ref: it keeps "
            "the mainline history alive"
        )
    scoped = {entry.run_id for entry in repo.get_reflog(name)}
    if not scoped and name not in repo.list_refs():
        # A failed ref creation can leave a listed ref with an empty
        # history, so existence is checked against the stored names too.
        raise RefNotFoundError(name)
    # Every read comes before the first delete, so a raise leaves the
    # repository as it was.
    keep = _find_reachable_runs(repo, exclude=name)
    repo.delete_ref_unchecked(name)
    deleted = [
        run_id
        for run_id in sorted(scoped - keep)
        if repo.delete_run_unchecked(run_id) is not None
    ]
    return RefDeletion(ref=name, deleted_runs=deleted)


def delete_run_if_unreferenced(repo: RunRepository, run_id: str) -> str | None:
    """Delete a run only if no ref reaches it, at its tip or in its history.

    Raises:
        RunReferencedError: naming the refs that still reach it.
        CorruptRecordError: when a reflog does not parse. The run stays,
            since the unread entries can name it.
        InvalidIdentifierError: when `run_id` is not a valid run id, or a foreign
            key sits under the refs prefix.
    """
    # Before the scan, whose exact-match comparison treats an id in another
    # case as unreferenced.
    ensure_run_id(run_id)
    referencing = _find_refs_referencing_run(repo, run_id)
    if referencing:
        raise RunReferencedError(run_id, referencing)
    return repo.delete_run_unchecked(run_id)


def _find_reachable_runs(
    repo: RunRepository, *, exclude: str | None = None
) -> set[str]:
    """Run ids named anywhere in any reflog other than `exclude`. Raises
    on the first reflog that cannot be read, since a partial set loses the
    runs that the unread entries hold."""
    keep: set[str] = set()
    for name in repo.list_refs():
        if name == exclude:
            continue
        for entry in repo.get_reflog(name):
            keep.add(entry.run_id)
    return keep


def _find_refs_referencing_run(repo: RunRepository, run_id: str) -> list[str]:
    """Every ref that points at `run_id` or once did."""
    return [
        name
        for name in repo.list_refs()
        if any(entry.run_id == run_id for entry in repo.get_reflog(name))
    ]
