"""The dashboard reads a stored run once, however many of its panes open, and
never serves a run that it deleted."""

from collections import Counter

from evaltrack.core.run_record import RunRecord
from evaltrack.repositories import RunRepository
from evaltrack.ui.run_cache import RunCache

from ..fakes import MemoryStore
from .conftest import make_recorded_run, make_repo_client


class CountingStore(MemoryStore):
    """Counts the reads of each stored run body."""

    def __init__(self) -> None:
        super().__init__()
        self.run_reads: Counter[str] = Counter()

    def read(self, path: str) -> bytes:
        if path.startswith("runs/"):
            self.run_reads[path] += 1
        return super().read(path)


def _save_run(repo: RunRepository) -> RunRecord:
    run = make_recorded_run()
    repo.save_run(run)
    return run


def test_a_run_and_its_panes_read_the_stored_run_once() -> None:
    store = CountingStore()
    repo = RunRepository(store)
    run = _save_run(repo)
    base = f"/api/repositories/main/runs/{run.id}"
    with make_repo_client(repo) as client:
        assert client.get(base).status_code == 200
        for _ in range(3):
            r = client.get(
                f"{base}/cases", params={"test": "test_x", "case": "test_case"}
            )
            assert r.status_code == 200
        assert client.get(f"{base}/download").status_code == 200
    assert store.run_reads[f"runs/{run.id}.json"] == 1


def test_a_deleted_run_is_not_served_from_the_cache() -> None:
    repo = RunRepository(MemoryStore())
    run = _save_run(repo)
    url = f"/api/repositories/main/runs/{run.id}"
    with make_repo_client(repo) as client:
        assert client.get(url).status_code == 200
        assert client.delete(url).status_code == 200
        assert client.get(url).status_code == 404


def test_a_run_deleted_with_its_ref_is_not_served_from_the_cache() -> None:
    repo = RunRepository(MemoryStore())
    run = _save_run(repo)
    repo.move_ref("pr/1", run.id)
    url = f"/api/repositories/main/runs/{run.id}"
    with make_repo_client(repo) as client:
        assert client.get(url).status_code == 200
        deletion = client.delete("/api/repositories/main/refs/pr/1").json()
        assert deletion["deleted_runs"] == [run.id]
        assert client.get(url).status_code == 404


def test_a_missing_run_is_not_cached() -> None:
    """A run can be pushed while the dashboard is open."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run()
    url = f"/api/repositories/main/runs/{run.id}"
    with make_repo_client(repo) as client:
        assert client.get(url).status_code == 404
        repo.save_run(run)
        assert client.get(url).status_code == 200


def test_the_least_recently_used_run_leaves_first() -> None:
    store = CountingStore()
    repo = RunRepository(store)
    first, second, third = (_save_run(repo) for _ in range(3))
    cache = RunCache(size=2)
    for run in (first, second, first, third, first, second):
        cache.load("main", repo=repo, run_id=run.id)
    reads = {
        run.id: store.run_reads[f"runs/{run.id}.json"] for run in (first, second, third)
    }
    assert reads == {first.id: 1, second.id: 2, third.id: 1}
