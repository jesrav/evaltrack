"""The dashboard's two DELETE routes: what they cascade, what they refuse, and
the cross-origin guard that gates both."""

from evaltrack.repositories import RunRepository

from ..factories import make_round
from ..fakes import MemoryStore
from .conftest import (
    make_corrupt_reflog_repository,
    make_recorded_run,
    make_repo_client,
)


def test_delete_ref_cascades_to_runs(populated_repository: RunRepository) -> None:
    pr_run = populated_repository.get_ref("pr/42")
    assert pr_run is not None
    with make_repo_client(populated_repository) as client:
        r = client.delete("/api/repositories/main/refs/pr/42")
    assert r.status_code == 200
    assert r.json()["deleted_runs"] == [pr_run.run_id]
    assert populated_repository.get_ref("pr/42") is None
    assert populated_repository.load_run(pr_run.run_id) is None


def test_delete_zombie_ref_with_empty_reflog_succeeds() -> None:
    """A crash between creating a reflog and its first append leaves a zero-byte
    object. It still lists as a ref, so a delete must succeed with an empty
    cascade. A 404 would make it impossible to remove from the dashboard."""
    store = MemoryStore()
    repo = RunRepository(store)
    store.write(b"", "refs/pr/13.log.jsonl")
    with make_repo_client(repo) as client:
        listed = client.get("/api/repositories/main/refs").json()
        assert [ref["name"] for ref in listed] == ["pr/13"], (
            "sanity: the zombie ref still lists"
        )
        r = client.delete("/api/repositories/main/refs/pr/13")
    assert r.status_code == 200
    assert r.json()["deleted_runs"] == [], "an empty history cascades no runs"
    assert list(repo.list_refs()) == []


def test_delete_missing_ref_404(populated_repository: RunRepository) -> None:
    """A typo'd delete must not report success. A nonexistent ref is a missing
    resource (404), not an empty cascade."""
    with make_repo_client(populated_repository) as client:
        r = client.delete("/api/repositories/main/refs/pr/999")
    assert r.status_code == 404
    assert "pr/999" in r.json()["detail"]


def test_delete_ref_refuses_while_its_reflog_is_unreadable() -> None:
    """The confirm dialog promises a cascade, and a cascade over an unreadable
    history could delete a run the unread line references. The delete refuses
    with the error naming the line to repair, and touches nothing."""
    setup = make_corrupt_reflog_repository()
    with make_repo_client(setup.repo) as client:
        r = client.delete("/api/repositories/main/refs/pr/1")
    assert r.status_code == 422
    assert "refs/pr/1.log.jsonl" in r.json()["detail"]
    assert list(setup.repo.list_refs()) == ["pr/1"], (
        "the refused delete left the ref in place"
    )
    assert setup.repo.load_run(setup.run_id) is not None, (
        "the refused delete left the run in place"
    )


def test_delete_baseline_ref_refused_409(
    populated_repository: RunRepository,
) -> None:
    """DELETE on the reserved `baseline` ref is refused, because it would
    cascade-delete the entire promoted history. That history must be intact
    afterwards."""
    baseline = populated_repository.get_ref("baseline")
    assert baseline is not None
    with make_repo_client(populated_repository) as client:
        r = client.delete("/api/repositories/main/refs/baseline")
        assert r.status_code == 409
        assert "baseline" in r.json()["detail"]
        # The ref, its reflog, and its run are all still readable.
        assert client.get("/api/repositories/main/refs/baseline").status_code == 200
        log = client.get("/api/repositories/main/reflogs/baseline").json()
        assert [e["run_id"] for e in log] == [baseline.run_id]
        run = client.get(f"/api/repositories/main/runs/{baseline.run_id}")
        assert run.status_code == 200


def test_delete_unreferenced_run() -> None:
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)  # unreferenced: no ref points at it
    with make_repo_client(repo) as client:
        r = client.delete(f"/api/repositories/main/runs/{run.id}")
    assert r.status_code == 200
    assert r.json()["id"] == run.id
    assert repo.load_run(run.id) is None


def test_delete_referenced_run_409(populated_repository: RunRepository) -> None:
    pr = populated_repository.get_ref("pr/42")
    assert pr is not None
    with make_repo_client(populated_repository) as client:
        r = client.delete(f"/api/repositories/main/runs/{pr.run_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["refs"] == ["pr/42"]
    assert populated_repository.load_run(pr.run_id) is not None, (
        "the refused delete left the run untouched"
    )


def test_delete_run_the_readable_tip_references_409s_past_a_torn_line() -> None:
    """The good entry above the tear already proves the run referenced, so the
    refusal is the ordinary one and names the ref, torn line or not."""
    setup = make_corrupt_reflog_repository()
    with make_repo_client(setup.repo) as client:
        r = client.delete(f"/api/repositories/main/runs/{setup.run_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["refs"] == ["pr/1"]
    assert setup.repo.load_run(setup.run_id) is not None, (
        "the refused delete left the run in place"
    )


def test_delete_run_refuses_while_a_reflog_is_unreadable() -> None:
    """The guarded delete must not read "cannot tell" as "unreferenced". The run
    can be exactly what the unreadable line references, and that delete is not
    recoverable. The refusal carries the error naming the line to repair."""
    setup = make_corrupt_reflog_repository()
    stray = make_recorded_run(make_round(), commit="c1")
    setup.repo.save_run(stray)  # no readable entry names it, only maybe the torn one
    with make_repo_client(setup.repo) as client:
        r = client.delete(f"/api/repositories/main/runs/{stray.id}")
    assert r.status_code == 422
    assert "refs/pr/1.log.jsonl" in r.json()["detail"]
    assert setup.repo.load_run(stray.id) is not None, (
        "the refused delete left the run in place"
    )


def test_delete_missing_run_404(populated_repository: RunRepository) -> None:
    with make_repo_client(populated_repository) as client:
        r = client.delete("/api/repositories/main/runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC")
    assert r.status_code == 404


def test_delete_run_rejects_cross_origin(populated_repository: RunRepository) -> None:
    summaries = list(populated_repository.list_runs())
    with make_repo_client(populated_repository) as client:
        r = client.delete(
            f"/api/repositories/main/runs/{summaries[2].id}",
            headers={"origin": "https://evil.example"},
        )
    assert r.status_code == 403
