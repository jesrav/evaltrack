"""Saving, loading and listing runs in a `RunRepository`.

Also the summary sidecars that back the listing.
"""

import json
import logging
from collections.abc import Iterator

import pytest

from evaltrack.core.errors import (
    CorruptRecordError,
    InvalidIdentifierError,
    UnsupportedSchemaError,
)
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    dump_run_json,
)
from evaltrack.repositories import RunRepository, delete_run_if_unreferenced
from evaltrack.repositories.store import ObjectNotFoundError, ObjectStore

from ..factories import evaluate_awkward_report, translate_report
from ..fakes import MemoryStore
from .helpers import RepositoryFactory, StoreFactory, make_run

# --- RunRepository: runs ---


def test_save_then_load_run(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    run = make_run(commit="abc123")
    repo.save_run(run)
    assert repo.load_run(run.id) == run


def test_save_then_load_run_recorded_from_a_real_eval(
    repository_factory: RepositoryFactory,
) -> None:
    """Every backend returns the bytes it was given, for a run recorded from a
    real `Dataset.evaluate`.

    What those bytes mean is `tests/core/test_run_record.py`'s: it pins how a
    non-finite metric, a repr-only object and deep nesting are written and read
    back. What is backend-specific is fidelity. A store that re-encoded or
    normalized on the way through would corrupt a run that dumps and parses
    perfectly well, and a run that saves but never loads has lost the history it
    was meant to keep. A real eval supplies the awkward bytes, because
    a hand-built run has none.
    """
    nodeid = "tests/test_awkward.py::test_awkward"
    recorder = EvalRecorder()
    recorder.add_round(nodeid, translate_report(evaluate_awkward_report()))
    recorder.set_test_outcome(nodeid, "passed")
    run = recorder.to_run_record()

    repo = repository_factory()
    repo.save_run(run)
    loaded = repo.load_run(run.id)

    assert loaded is not None
    # Equivalence is the JSON, not object identity. An opaque value with no JSON
    # form is stored as its repr, so what round-trips is the run as it lands on
    # disk.
    assert dump_run_json(loaded) == dump_run_json(run)
    assert loaded.tests[nodeid].cases["awkward_case"].outcome == "passed", (
        "the structure has to survive the store, not only the byte count"
    )


def test_load_missing_run_returns_none(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    assert repo.load_run("01J9Z3QW2KJ5H8VN4TQY7B6MDC") is None


def test_a_run_id_in_another_case_is_refused(
    repository_factory: RepositoryFactory,
) -> None:
    """A ULID is uppercase, so a lowercase spelling of one names nothing. A
    read refuses it rather than answering with a silent `None` that reads as
    `the run is gone`, and a write refuses it rather than recording a pointer
    that later comparisons would miss."""
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    lowered = run.id.lower()

    with pytest.raises(InvalidIdentifierError):
        repo.load_run(lowered)
    with pytest.raises(InvalidIdentifierError):
        repo.get_run_summary(lowered)
    with pytest.raises(InvalidIdentifierError):
        repo.delete_run_unchecked(lowered)
    with pytest.raises(InvalidIdentifierError):
        delete_run_if_unreferenced(repo, lowered)
    with pytest.raises(InvalidIdentifierError):
        repo.move_ref("pr/7", lowered)
    assert repo.load_run(run.id) == run, (
        "the refused calls must leave the saved run untouched"
    )


def test_save_run_under_an_existing_id_replaces(
    repository_factory: RepositoryFactory,
) -> None:
    """Content is not compared, so the last save wins. The ULID uniqueness
    contract is what keeps that safe."""
    repo = repository_factory()
    run = make_run(labels={"tag": "first"})
    repo.save_run(run)
    repo.save_run(run.model_copy(update={"labels": {"tag": "second"}}))
    loaded = repo.load_run(run.id)
    assert loaded is not None
    assert loaded.labels == {"tag": "second"}
    [summary] = list(repo.list_runs())
    assert summary.labels == {"tag": "second"}, "the sidecar must follow the run"


def test_list_runs_newest_first(repository_factory: RepositoryFactory) -> None:
    repo = repository_factory()
    runs = [make_run() for _ in range(3)]
    for run in runs:
        repo.save_run(run)
    summaries = list(repo.list_runs())
    assert [s.id for s in summaries] == sorted([r.id for r in runs], reverse=True)


def test_list_runs_returns_summary_not_full_run(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    repo.save_run(make_run(commit="abc"))
    [summary] = list(repo.list_runs())
    assert summary.commit == "abc"


def test_get_run_summary_finds_one_run(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    wanted = make_run(commit="abc")
    repo.save_run(make_run(commit="other"))
    repo.save_run(wanted)
    summary = repo.get_run_summary(wanted.id)
    assert summary is not None
    assert (summary.id, summary.commit) == (wanted.id, "abc")


def test_get_run_summary_is_none_for_a_missing_run(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    assert repo.get_run_summary("01JZZZZZZZZZZZZZZZZZZZZZZZ") is None


class _SpyStore(ObjectStore):
    """Wraps a store and records every read path, to prove what `list_runs` touches."""

    def __init__(self, inner: ObjectStore) -> None:
        self._inner = inner
        self.reads: list[str] = []

    def read(self, path: str) -> bytes:
        self.reads.append(path)
        return self._inner.read(path)

    def write(self, content: bytes, path: str) -> None:
        self._inner.write(content, path)

    def append(self, content: bytes, path: str) -> None:
        self._inner.append(content, path)

    def delete(self, path: str) -> None:
        self._inner.delete(path)

    def list(self, prefix: str) -> Iterator[str]:
        return self._inner.list(prefix)


def test_list_runs_reads_only_summary_sidecars_not_full_bodies() -> None:
    spy = _SpyStore(MemoryStore())
    repo = RunRepository(spy)
    for _ in range(3):
        repo.save_run(make_run())
    spy.reads.clear()
    list(repo.list_runs())
    assert spy.reads, "list_runs read nothing"
    assert all(p.startswith("summaries/") for p in spy.reads), spy.reads


def test_get_run_summary_reads_one_sidecar_not_the_listing() -> None:
    """This is the point of the lookup by id. A ref tip must not cost a
    whole-repository listing per ref."""
    spy = _SpyStore(MemoryStore())
    repo = RunRepository(spy)
    runs = [make_run() for _ in range(3)]
    for run in runs:
        repo.save_run(run)
    spy.reads.clear()
    repo.get_run_summary(runs[0].id)
    assert spy.reads == [f"summaries/{runs[0].id}.json"]


def test_list_runs_lists_a_run_with_no_sidecar_from_its_body() -> None:
    """A save that died between its two writes leaves a run with no sidecar.
    The listing projects the summary from the body and writes nothing back,
    so a read-only reader lists the same as a writer."""
    store = MemoryStore()
    repo = RunRepository(store)
    run = make_run(commit="c0ffee")
    repo.save_run(run)
    store.delete(f"summaries/{run.id}.json")

    [summary] = list(RunRepository(store).list_runs())
    assert summary.id == run.id
    assert summary.commit == "c0ffee"
    assert f"summaries/{run.id}.json" not in list(store.list("summaries/"))


def test_a_sidecar_keeps_fields_it_does_not_declare(
    store_factory: StoreFactory,
) -> None:
    """A sidecar is stored, so it takes the rule every stored model takes: a
    field a newer evaltrack wrote survives the load and the next dump. One that
    strips them loses recorded data on every path that copies a summary."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    sidecar = json.loads(store.read(f"summaries/{run.id}.json"))
    sidecar["tests_flaky"] = 2
    store.write(json.dumps(sidecar).encode(), f"summaries/{run.id}.json")

    summary = repo.get_run_summary(run.id)
    assert summary is not None
    assert json.loads(summary.model_dump_json())["tests_flaky"] == 2


@pytest.mark.parametrize("key", ["notes:2024", "not-a-ulid"])
def test_list_runs_skips_an_invalid_run_key(
    store_factory: StoreFactory, key: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A foreign object planted under `runs/` must not take the whole listing
    down. No read path can return it as a run, so a skip loses nothing. Neither
    key is a valid run id. One holds a character a name cannot hold, and the
    other is not a ULID."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    planted = b'{"note": "put here out of band"}'
    store.write(planted, f"runs/{key}.json")

    with caplog.at_level(logging.WARNING):
        summaries = list(repo.list_runs())
    assert f"runs/{key}.json" in caplog.text, (
        "the skipped key must be named in a warning"
    )

    assert [summary.id for summary in summaries] == [run.id]
    assert store.read(f"runs/{key}.json") == planted, (
        "the foreign object must be left untouched"
    )


def test_list_runs_fails_loudly_on_an_unreadable_run_body(
    store_factory: StoreFactory,
) -> None:
    """A run body is written whole, so one that no longer parses is damage the
    listing cannot paper over. The error names the object to delete."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    # Without the sidecar the listing has to read the body.
    store.delete(f"summaries/{run.id}.json")
    store.write(b'{"id": "x", "truncated": ', f"runs/{run.id}.json")

    with pytest.raises(CorruptRecordError) as excinfo:
        list(repo.list_runs())
    assert f"runs/{run.id}.json" in str(excinfo.value)


def test_load_run_fails_loudly_on_an_unreadable_body(
    store_factory: StoreFactory,
) -> None:
    """A request for one run by id is the read where the operator finds out what
    is wrong and which object to delete."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    store.write(b'{"id": "x", "truncated": ', f"runs/{run.id}.json")

    with pytest.raises(CorruptRecordError) as excinfo:
        repo.load_run(run.id)
    message = str(excinfo.value)
    assert run.id in message
    assert f"runs/{run.id}.json" in message


# --- RunRepository: run schema versions ---


def test_saved_run_json_carries_the_schema_version(
    store_factory: StoreFactory,
) -> None:
    """The stamp in the stored bytes is what lets a future reader tell format
    skew apart from corruption, so every new run must carry it."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    stored = json.loads(store.read(f"runs/{run.id}.json"))
    assert stored["run_schema_version"] == RUN_SCHEMA_VERSION == 1, (
        "a schema bump must be deliberate: update the pin too"
    )


def test_load_run_reports_another_stored_format_as_skew_not_corruption(
    store_factory: StoreFactory,
) -> None:
    """A run written in a future format is readable history to a newer install,
    not damage. The error must say to upgrade, never to delete."""
    store = store_factory()
    repo = RunRepository(store)
    run_id = make_run().id
    newer = RUN_SCHEMA_VERSION + 1
    store.write(
        f'{{"run_schema_version": {newer}, "future": "shape"}}'.encode(),
        f"runs/{run_id}.json",
    )

    with pytest.raises(UnsupportedSchemaError) as excinfo:
        repo.load_run(run_id)
    message = str(excinfo.value)
    assert "run schema" in message
    assert f"run schema {newer}" in message
    assert "this evaltrack reads" in message
    assert "Delete" not in message, "skew must never advise deleting readable history"


def test_a_run_at_another_schema_leaves_the_rest_of_the_repository_readable(
    store_factory: StoreFactory,
) -> None:
    """A run body this install cannot read costs that one run and not the
    whole repository."""
    store = store_factory()
    repo = RunRepository(store)
    readable, unreadable = make_run(), make_run()
    repo.save_run(readable)
    repo.save_run(unreadable)
    repo.move_ref("baseline", readable.id)
    # Rewrite one body as a newer evaltrack would, leaving its sidecar in place.
    store.write(
        f'{{"run_schema_version": {RUN_SCHEMA_VERSION + 1}, "future": "shape"}}'.encode(),
        f"runs/{unreadable.id}.json",
    )

    assert {summary.id for summary in repo.list_runs()} == {readable.id, unreadable.id}
    assert list(repo.list_refs()) == ["baseline"]
    tip = repo.get_ref("baseline")
    assert tip is not None and tip.run_id == readable.id
    assert repo.load_run(readable.id) is not None
    with pytest.raises(UnsupportedSchemaError):
        repo.load_run(unreadable.id)


def test_list_runs_skips_a_newer_schema_run_without_calling_it_damaged(
    store_factory: StoreFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """With no sidecar the listing has to read the body, which is where a run
    from a newer evaltrack is refused. It is skipped, but the
    operator must not be told its bytes are broken: the repair for that is a
    delete, and this run is whole history that a newer evaltrack still reads.
    """
    store = store_factory()
    repo = RunRepository(store)
    good = make_run()
    repo.save_run(good)
    newer = make_run()
    repo.save_run(newer)
    # A run a newer evaltrack wrote, whose sidecar this one cannot project
    # either, so nothing is left to list it from.
    future = json.loads(dump_run_json(newer))
    future["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    store.write(json.dumps(future).encode(), f"runs/{newer.id}.json")
    store.delete(f"summaries/{newer.id}.json")

    with caplog.at_level(logging.WARNING):
        summaries = list(repo.list_runs())

    assert [summary.id for summary in summaries] == [good.id]
    warning = caplog.text
    assert newer.id in warning, "the skipped run must be named"
    assert f"run schema {RUN_SCHEMA_VERSION + 1}" in warning, (
        "the warning must name the format the run was recorded in"
    )
    assert "intact" in warning
    assert "does not parse" not in warning, "an intact run is not unparseable"
    assert "corrupt" not in warning.lower(), "an intact run is not corrupt"
    assert "Delete" not in warning, "the listing must never advise deleting it"
    with pytest.raises(UnsupportedSchemaError):
        repo.load_run(newer.id)


def test_a_newer_schema_run_is_not_caught_by_a_corrupt_record_handler(
    store_factory: StoreFactory,
) -> None:
    """A caller that deletes what it cannot read writes `except
    CorruptRecordError`, and the corrupt-body message tells it to. Catching a
    newer evaltrack's run there destroys good history."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    future = json.loads(dump_run_json(run))
    future["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    store.write(json.dumps(future).encode(), f"runs/{run.id}.json")

    with pytest.raises(UnsupportedSchemaError):
        try:
            repo.load_run(run.id)
        except CorruptRecordError:
            repo.delete_run_unchecked(run.id)
    assert store.read(f"runs/{run.id}.json"), "the run must survive the delete path"


def test_another_stored_format_is_refused_even_when_it_still_parses(
    store_factory: StoreFactory,
) -> None:
    """Additive changes do not bump the stamp (unknown fields are kept), so a
    bumped stamp means an existing field changed meaning. A clean parse proves
    nothing then, and a load that proceeds anyway silently misreads the run.
    The error must say to upgrade, never to delete."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    future = json.loads(dump_run_json(run))
    future["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    future["field_from_the_future"] = True
    store.write(json.dumps(future).encode(), f"runs/{run.id}.json")

    with pytest.raises(UnsupportedSchemaError) as excinfo:
        repo.load_run(run.id)
    message = str(excinfo.value)
    assert "run schema" in message
    assert f"run schema {RUN_SCHEMA_VERSION + 1}" in message
    assert "this evaltrack reads" in message
    assert "Delete" not in message, "skew must never advise deleting readable history"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"{not json at all", id="unparseable"),
        pytest.param(
            f'{{"run_schema_version": {RUN_SCHEMA_VERSION}, "unexpected": "shape"}}'.encode(),
            id="current-version",
        ),
    ],
)
def test_load_run_keeps_the_delete_advice_when_the_version_matches(
    store_factory: StoreFactory, content: bytes
) -> None:
    """Bytes without a newer stamp are damage, not skew. An upgrade cannot fix
    them, so the advice stays delete."""
    store = store_factory()
    repo = RunRepository(store)
    run_id = make_run().id
    store.write(content, f"runs/{run_id}.json")

    with pytest.raises(CorruptRecordError) as excinfo:
        repo.load_run(run_id)
    message = str(excinfo.value)
    assert "Delete the run" in message
    assert "Upgrade" not in message, "an upgrade cannot fix damaged bytes"


def test_delete_run_removes_summary_sidecar(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    repo.delete_run_unchecked(run.id)
    assert list(repo.list_runs()) == [], (
        "a deleted run must not resurrect in the listing"
    )
    assert repo.load_run(run.id) is None


class _CrashingDeleteStore(_SpyStore):
    """Fails every delete after the first, to pin the order `delete_run` uses."""

    def __init__(self, inner: ObjectStore) -> None:
        super().__init__(inner)
        self.deleted: list[str] = []

    def delete(self, path: str) -> None:
        if self.deleted:
            raise RuntimeError("store went away mid-delete")
        self.deleted.append(path)
        self._inner.delete(path)


def test_delete_run_interrupted_leaves_no_orphan_sidecar() -> None:
    """A sidecar whose run body is gone is invisible to listing, so it
    would leak forever. The surviving half must be the body, which lists fine
    off a rebuilt sidecar."""
    store = _CrashingDeleteStore(MemoryStore())
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)

    with pytest.raises(RuntimeError):
        repo.delete_run_unchecked(run.id)

    with pytest.raises(ObjectNotFoundError):
        store.read(f"summaries/{run.id}.json")
    assert [summary.id for summary in repo.list_runs()] == [run.id], (
        "the surviving body must still be listable"
    )


def test_delete_run_reclaims_a_sidecar_left_by_an_earlier_crash(
    store_factory: StoreFactory,
) -> None:
    """A stray sidecar can outlive its run body. A second delete of the run
    reclaims it and still reports the run as absent, because it is."""
    store = store_factory()
    repo = RunRepository(store)
    run = make_run()
    repo.save_run(run)
    store.delete(f"runs/{run.id}.json")

    assert repo.delete_run_unchecked(run.id) is None, (
        "the run must report absent when only its sidecar remains"
    )
    with pytest.raises(ObjectNotFoundError):
        store.read(f"summaries/{run.id}.json")


def test_the_sidecar_records_the_stored_size(
    repository_factory: RepositoryFactory,
) -> None:
    repo = repository_factory()
    run = make_run()
    repo.save_run(run)
    [summary] = repo.list_runs()
    assert summary.size_bytes == len(dump_run_json(run))
