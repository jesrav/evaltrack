"""`RunRepository` and the list-view type it yields. Runs and refs are stored
as bytes at well-known paths in an `ObjectStore`.

Layout. This is the first one, so it carries no version stamp. A later layout
can tell this one by `runs/` existing with no stamp.

    runs/{id}.json                 RunRecord
    summaries/{id}.json            list-cheap RunSummary sidecar (derived)
    refs/{name}.log.jsonl          append-only reflog. Its last entry is the
                                   ref's current target (no separate pointer)

The sidecar exists so `list_runs` never reads a run body. A missing sidecar
is re-projected from the run on demand.

There is no separate pointer file for a ref, so two `move_ref` calls that run
at the same time cannot lose an update.

A sidecar carries no schema stamp and parses under any version. So a
`RUN_SCHEMA_VERSION` bump has to delete the stored sidecars as well. Without
that, a listing keeps offering runs that fail when they are opened.
"""

import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from evaltrack.core.errors import (
    CorruptRecordError,
    InvalidIdentifierError,
    UnsupportedSchemaError,
)
from evaltrack.core.refs import (
    ReflogEntry,
    ensure_lowercase_ref_name,
    parse_reflog_entry,
)
from evaltrack.core.run_record import (
    FAILING_OUTCOMES,
    RunId,
    RunRecord,
    dump_run_json,
    ensure_run_id,
    parse_run_json,
)
from evaltrack.repositories.store import ObjectNotFoundError, ObjectStore

_RUNS_PREFIX = "runs/"
_SUMMARIES_PREFIX = "summaries/"
_REFS_PREFIX = "refs/"
_REFLOG_SUFFIX = ".log.jsonl"
# Below the shortest key a backend accepts, whether that limit covers one path
# component or the whole key.
_MAX_KEY_LENGTH = 200

# `.` and `..` match too, so a separate check refuses them.
_REF_SEGMENT_CHARS = re.compile(r"[a-z0-9._-]+")

# `TypeError` covers valid JSON that is not an object. `UnicodeDecodeError`
# covers a line torn inside a multi-byte character.
_CORRUPT_LINE_ERRORS = (
    json.JSONDecodeError,
    UnicodeDecodeError,
    ValidationError,
    TypeError,
)

_REFLOG_RECOVERY_HINT = (
    "A crash mid-append or an edit by hand can leave such a line. The repair "
    "steps are in docs/repositories.md#repairing-a-torn-reflog."
)

_logger = logging.getLogger(__name__)


class RunSummary(BaseModel):
    """Small projection of `RunRecord` for list views. Never holds reports.

    Attributes:
        id: The run's ULID.
        created_at: When the run was recorded.
        commit: The commit the run was evaluated at, if known.
        worktree_dirty: Whether the working tree was dirty at eval time, if known.
        labels: The run's labels.
        tests_total: How many tests the run recorded.
        tests_failed: How many tests failed or errored.
        size_bytes: The stored run's size. None for a run saved before the size
            was recorded, and for a summary rebuilt from the run itself.
    """

    # Stored as a sidecar, so it takes the same rule as every other stored
    # model: a field a newer evaltrack wrote must not stop an older one reading.
    model_config = ConfigDict(extra="allow")

    id: RunId
    created_at: AwareDatetime
    commit: str | None = None
    worktree_dirty: bool | None = None
    labels: dict[str, str] = {}
    tests_total: int
    tests_failed: int
    size_bytes: int | None = None

    @classmethod
    def from_run(cls, run: RunRecord, *, size_bytes: int | None = None) -> "RunSummary":
        return cls(
            id=run.id,
            created_at=run.created_at,
            commit=run.commit,
            worktree_dirty=run.worktree_dirty,
            labels=run.labels,
            tests_total=len(run.tests),
            tests_failed=sum(
                1 for test in run.tests.values() if test.outcome in FAILING_OUTCOMES
            ),
            size_bytes=size_bytes,
        )


def _ensure_safe_ref_name(name: str) -> str:
    """A ref name becomes a storage key and a URL path segment, so the allowed
    set is what is safe in both. ASCII, so no two names fold together."""
    value = ensure_lowercase_ref_name(name)
    segments = value.split("/")
    if any(s in (".", "..") or not _REF_SEGMENT_CHARS.fullmatch(s) for s in segments):
        raise InvalidIdentifierError(
            f"{value!r} cannot be used as a ref name: a name is one or more "
            "`/`-separated segments of lowercase letters, digits, `.`, `_` and "
            "`-`, and no segment is `.` or `..` (e.g. `pr/123`)"
        )
    if len(value) > _MAX_KEY_LENGTH:
        raise InvalidIdentifierError(
            f"{value!r} cannot be used as a ref name: the whole name must be at "
            f"most {_MAX_KEY_LENGTH} characters, and this one is {len(value)}"
        )
    return value


def _build_run_path(run_id: str) -> str:
    return f"{_RUNS_PREFIX}{ensure_run_id(run_id)}.json"


def _build_summary_path(run_id: str) -> str:
    # Under their own prefix, so a reader listing `runs/*.json` never mistakes a
    # sidecar for a run.
    return f"{_SUMMARIES_PREFIX}{ensure_run_id(run_id)}.json"


def _build_reflog_path(name: str) -> str:
    return f"{_REFS_PREFIX}{_ensure_safe_ref_name(name)}{_REFLOG_SUFFIX}"


class RunRepository:
    """A repository of immutable runs and mutable refs, kept in an `ObjectStore`.
    Only the store differs between backends.

    Names and ids are checked, never corrected. A run id is a ULID and a ref
    name is lowercase, so every comparison over a stored name or id is an exact
    match.

    Any method can raise `RepositoryUnavailableError` when the storage cannot
    serve the request.
    """

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def verify_available(self) -> None:
        """Cheaply check that the backing storage can serve requests.

        Raises:
            ValueError: when the storage is misconfigured.
            RepositoryUnavailableError: when the storage cannot be reached.
        """
        self._store.verify_available()

    def verify_writable(self) -> None:
        """Cheaply check, without network I/O, that a save can succeed.

        Raises:
            RepositoryUnavailableError: when a save cannot succeed.
        """
        self._store.verify_writable()

    # --- runs ---

    def save_run(self, run: RunRecord) -> None:
        """Save a run. A save under an existing `run.id` replaces the stored run.
        Content is not compared, so an id must never be reused for different
        content.

        Raises:
            RepositoryUnavailableError: when the storage cannot serve the write.
        """
        data = dump_run_json(run)
        self._store.write(data, _build_run_path(run.id))
        # After the run, so a crash between the two leaves a run with no
        # sidecar, which a listing heals.
        self._write_summary(RunSummary.from_run(run, size_bytes=len(data)))

    def load_run(self, run_id: str) -> RunRecord | None:
        """Load one run in full, or `None` if absent.

        Raises:
            CorruptRecordError: when the stored run does not parse.
            UnsupportedSchemaError: when the run was recorded under a schema
                this version does not read. The stored run is intact.
        """
        path = _build_run_path(run_id)
        try:
            data = self._store.read(path)
        except ObjectNotFoundError:
            return None
        # A run at another schema is refused before the shape is read, so a
        # `ValidationError` here is damage and not skew.
        try:
            return parse_run_json(data)
        except ValidationError as exc:
            raise CorruptRecordError(
                f"unreadable run {run_id}: {path} does not parse as a stored "
                f"run ({exc}). Delete the run to reclaim the key."
            ) from exc

    def list_runs(self) -> Iterator[RunSummary]:
        """Yield one summary per run, newest-first. A run recorded under a schema
        this version does not read is skipped.

        Raises:
            CorruptRecordError: when a stored run does not parse.
        """
        # ULIDs sort lexically by time, so newest-first means reverse.
        for run_id in sorted(self._iter_run_ids(), reverse=True):
            try:
                summary = self._get_summary(run_id)
            except InvalidIdentifierError:
                # An invalid id is unreachable by any read path. A skip keeps one
                # foreign object from taking the listing down. Logged, not
                # warned, so `-W error` still gets the listing.
                _logger.warning(
                    "skipping %s%s.json: not a valid run id, so it cannot hold "
                    "a readable run",
                    _RUNS_PREFIX,
                    run_id,
                )
                continue
            except UnsupportedSchemaError as exc:
                # Skipped like a body that does not parse, but never described
                # as one: the bytes are intact and only a matching evaltrack
                # can read them.
                _logger.warning(
                    "skipping %s%s.json: the run is intact but was recorded by "
                    "another evaltrack, and reading it needs that version (%s)",
                    _RUNS_PREFIX,
                    run_id,
                    exc,
                )
                continue
            if summary is not None:
                yield summary

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        """One run's summary, or `None` if absent. One read, not a scan.

        Raises:
            CorruptRecordError: when the stored run does not parse.
        """
        ensure_run_id(run_id)
        try:
            return self._get_summary(run_id)
        except UnsupportedSchemaError:
            # Same skip as the listing: the run is whole, and only a
            # matching evaltrack reads it.
            return None

    def delete_run_unchecked(self, run_id: str) -> str | None:
        """Delete one run. The id if deleted, `None` if it did not exist.

        This is the storage primitive. It does not ask whether a ref still
        reaches the run, so a delete here can leave a ref that points at nothing.
        `delete_run_if_unreferenced` is the way to remove a run.
        """
        # Sidecar first, so a crash between the two leaves a run body the
        # listing heals, not a sidecar nothing lists or collects.
        try:
            self._store.delete(_build_summary_path(run_id))
        except ObjectNotFoundError:
            pass
        try:
            self._store.delete(_build_run_path(run_id))
        except ObjectNotFoundError:
            return None
        return run_id

    def _iter_run_ids(self) -> Iterator[str]:
        """Run ids as stored, without reading any run body."""
        for path in self._store.list(_RUNS_PREFIX):
            if path.endswith(".json"):
                yield path[len(_RUNS_PREFIX) : -len(".json")]

    def _write_summary(self, summary: RunSummary) -> None:
        self._store.write(
            summary.model_dump_json(indent=2).encode(),
            _build_summary_path(summary.id),
        )

    def _get_summary(self, run_id: str) -> RunSummary | None:
        """The stored sidecar, or one projected from the run body when the sidecar
        is missing. Never written back, because a read path that writes needs a
        policy for a store that refuses."""
        try:
            summary = RunSummary.model_validate_json(
                self._store.read(_build_summary_path(run_id))
            )
        except ObjectNotFoundError:
            run = self.load_run(run_id)
            return RunSummary.from_run(run) if run is not None else None
        return summary

    # --- refs ---

    def get_reflog(self, name: str) -> Iterator[ReflogEntry]:
        """Yield a ref's whole history, oldest-first. Nothing if the ref does not exist.

        Raises:
            CorruptRecordError: when an entry does not parse.
        """
        path = _build_reflog_path(name)
        try:
            data = self._store.read(path)
        except ObjectNotFoundError:
            return iter([])
        return _iter_jsonl(data, name=name, path=path)

    def get_ref(self, name: str) -> ReflogEntry | None:
        """The ref's current target (its newest reflog entry), or `None` if the ref does
        not exist.

        Raises:
            CorruptRecordError: when an entry does not parse.
        """
        tail = self.tail_reflog(name, 1)
        return tail[0] if tail else None

    def tail_reflog(self, name: str, n: int) -> list[ReflogEntry]:
        """The last `n` entries, newest-first. Empty when the ref does not exist or
        `n <= 0`.

        Raises:
            CorruptRecordError: when an entry does not parse.
        """
        entries = list(self.get_reflog(name))
        return entries[-n:][::-1] if n > 0 else []

    def validate_ref_name(self, name: str) -> str:
        """Check that a ref name is storable, without touching storage. Rejects
        exactly the names `move_ref` rejects.

        Raises:
            InvalidIdentifierError: when this repository cannot store a ref under it.
        """
        return _ensure_safe_ref_name(name)

    def move_ref(
        self,
        name: str,
        run_id: str,
        *,
        commit: str | None = None,
        pr: int | None = None,
        title: str | None = None,
    ) -> ReflogEntry:
        """Repoint a ref and append a reflog entry, atomically. The ref is created if
        absent.

        Idempotent, so a move to the run the ref already points at appends no
        entry, and records none of the metadata passed with it.

        Returns:
            The ref's tip entry after the move.

        Raises:
            InvalidIdentifierError: when no ref can be stored under `name`, or
                `run_id` is not a valid run id.
        """
        # Here, not in the entry model, so a bad id is reported as a naming error
        # and not as corrupt stored data.
        ensure_run_id(run_id)
        current = self.get_ref(name)
        if current is not None and current.run_id == run_id:
            return current
        entry = ReflogEntry(
            run_id=run_id,
            moved_at=datetime.now(UTC),
            commit=commit,
            pr=pr,
            title=title,
        )
        self._store.append(
            entry.model_dump_json().encode() + b"\n",
            _build_reflog_path(name),
        )
        return entry

    def delete_ref_unchecked(self, name: str) -> None:
        """Delete a ref by deleting its reflog. A name that does not exist is a
        no-op.

        This is the storage primitive, not the way to remove a ref. It skips
        every guard `delete_ref_and_orphaned_runs` applies, `baseline` included.
        Runs that only this ref reached stay stored, and nothing reclaims them.

        It reads no history, so it can still drop a ref whose reflog no longer
        parses.
        """
        try:
            self._store.delete(_build_reflog_path(name))
        except ObjectNotFoundError:
            pass

    def list_refs(self) -> Iterator[str]:
        """Enumerate ref names as stored, including a key that is not a valid ref
        name, because retention needs the whole set. Reading such a key raises
        `InvalidIdentifierError`."""
        for path in self._store.list(_REFS_PREFIX):
            if path.endswith(_REFLOG_SUFFIX):
                yield path[len(_REFS_PREFIX) : -len(_REFLOG_SUFFIX)]


def _iter_jsonl(data: bytes, *, name: str, path: str) -> Iterator[ReflogEntry]:
    for line_no, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = parse_reflog_entry(json.loads(line))
        except _CORRUPT_LINE_ERRORS as exc:
            raise CorruptRecordError(
                f"corrupt reflog for ref {name!r}: line {line_no} of {path} is not "
                f"a readable reflog entry ({exc}). {_REFLOG_RECOVERY_HINT}"
            ) from exc
        yield entry
