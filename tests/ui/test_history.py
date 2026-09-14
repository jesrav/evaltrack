"""The cross-run views: pooled reliability, the score history, and where a run
landed on the mainline. All three read the promoted `baseline` history."""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from evaltrack.core.run_record import RUN_SCHEMA_VERSION, RunRecord
from evaltrack.repositories import RunRepository, promote
from evaltrack.ui import MountedRepository, create_app

from ..factories import make_repeat_round, make_round
from ..fakes import MemoryStore
from .conftest import (
    make_client,
    make_corrupt_reflog_repository,
    make_recorded_run,
    make_repo_client,
)


@pytest.fixture
def reliability_client() -> Iterator[TestClient]:
    """A local-only repo: four saved runs but no `baseline` ref, so there is no
    mainline history to estimate reliability over."""
    repo = RunRepository(MemoryStore())
    for i, ok in enumerate([True, True, False, True]):
        repo.save_run(
            make_recorded_run(
                make_round(assertions={"passed": ok}),
                commit=f"c{i}",
                reliability_target=0.9,
            )
        )
    with make_repo_client(repo) as client:
        yield client


def _make_baseline_repo() -> RunRepository:
    """A repo whose `baseline` reflog has 4 promoted runs (3 pass, 1 fail), each
    promoted with a distinct mainline commit `main-{i}` (not the run's own
    branch commit)."""
    repo = RunRepository(MemoryStore())
    for i, ok in enumerate([True, True, False, True]):
        run = make_recorded_run(
            make_round(assertions={"passed": ok}),
            commit=f"branch-{i}",
            reliability_target=0.9,
        )
        repo.save_run(run)
        repo.move_ref("baseline", run.id, commit=f"main-{i}")
    return repo


def _make_scored_baseline_repo() -> RunRepository:
    """A repo whose `baseline` reflog has 3 promoted runs, each scoring the case
    lower than the one before and promoted as its own PR."""
    repo = RunRepository(MemoryStore())
    for i, quality in enumerate([0.9, 0.7, 0.5]):
        run = make_recorded_run(
            make_round(scores={"quality": quality}),
            commit=f"branch-{i}",
            score_bars={"quality": 0.4},
        )
        repo.save_run(run)
        repo.move_ref(
            "baseline", run.id, commit=f"main-{i}", pr=100 + i, title=f"PR {i}"
        )
    return repo


EMPTY_HISTORY = {"reliability": {}, "score_history": {}}


def test_cross_run_views_are_empty_without_a_baseline(
    reliability_client: TestClient,
) -> None:
    """Without a `baseline` history (local-only runs, from different or dirty
    branches) there is no cross-run signal. evaltrack does not pool incomparable
    runs by recency. The viewed run (`run_id`) changes nothing, because there is
    still no mainline to lay the viewed run over."""
    r = reliability_client.get("/api/repositories/main/history")
    assert r.status_code == 200
    assert r.json() == EMPTY_HISTORY
    runs = reliability_client.get("/api/repositories/main/runs").json()
    overlaid = reliability_client.get(
        f"/api/repositories/main/history?run_id={runs[0]['id']}"
    )
    assert overlaid.status_code == 200
    assert overlaid.json() == EMPTY_HISTORY, (
        "the viewed run still has no mainline to draw over"
    )


def test_both_views_come_from_one_request() -> None:
    """The run view needs the pass-rate and the score trend together, and they
    read the same history. One response carries both, so opening a run reads
    that history once."""
    with make_repo_client(_make_scored_baseline_repo()) as client:
        body = client.get("/api/repositories/main/history").json()

    assert set(body) == {"reliability", "score_history"}
    assert body["reliability"]["test_x"]["test_case"]["pooled_attempts"] == 3
    assert [t["score"] for t in body["score_history"]["test_x"]] == ["quality"]


def test_reliability_pools_baseline_reflog_with_mainline_commits() -> None:
    """The whole per-case shape must reach the client, keyed test -> case, with
    every point stamped by the promotion that put its run on main."""
    repo = _make_baseline_repo()
    with make_repo_client(repo) as client:
        body = client.get("/api/repositories/main/history").json()
    case = body["reliability"]["test_x"]["test_case"]
    assert case["pooled_attempts"] == 4
    assert case["rate"] == 0.75
    assert [p["commit"] for p in case["points"]] == [
        "main-0",
        "main-1",
        "main-2",
        "main-3",
    ], "points carry the promote commits, not the branch commits"
    assert all(p["off_mainline"] is False for p in case["points"]), (
        "no point is off the mainline without a viewed run"
    )
    assert case["diverged"] is False


def test_reliability_pools_a_re_promoted_run_once() -> None:
    """A rollback puts one run in the baseline reflog twice. It is still a single
    observation of the eval, so its attempts must not pool twice. Counted twice,
    the rate here reads 4/6 instead of 3/4."""
    repo = RunRepository(MemoryStore())
    restored = make_recorded_run(make_repeat_round([True, False]))
    reverted = make_recorded_run(make_repeat_round([True, True]))
    repo.save_run(restored)
    repo.save_run(reverted)
    repo.move_ref("baseline", restored.id, commit="main-0")
    repo.move_ref("baseline", reverted.id, commit="main-1")
    repo.move_ref("baseline", restored.id, commit="main-2")  # rolled back to `restored`

    with make_repo_client(repo) as client:
        body = client.get("/api/repositories/main/history").json()

    case = body["reliability"]["test_x"]["c"]

    assert case["pooled_attempts"] == 4
    assert case["rate"] == 0.75
    assert [p["run_id"] for p in case["points"]] == [reverted.id, restored.id]
    assert [p["commit"] for p in case["points"]] == ["main-1", "main-2"], (
        "the restored run sits at the promotion that put it back on main"
    )


def test_reliability_skips_a_newer_schema_run_in_the_baseline_window() -> None:
    """A run a newer evaltrack promoted onto baseline must not 501 the pooled
    view. It cannot be measured here, so it is dropped from the pool, and the
    runs this evaltrack does read still answer."""
    store = MemoryStore()
    repo = RunRepository(store)
    ids: list[str] = []
    for i in range(2):
        run = make_recorded_run(
            make_round(assertions={"passed": True}),
            commit=f"c{i}",
            reliability_target=0.9,
        )
        repo.save_run(run)
        repo.move_ref("baseline", run.id)
        ids.append(run.id)
    newer_id = ids[0]
    stored = json.loads(store.read(f"runs/{newer_id}.json"))
    stored["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    store.write(json.dumps(stored).encode(), f"runs/{newer_id}.json")

    with make_repo_client(repo) as client:
        r = client.get("/api/repositories/main/history")
        viewed = client.get(f"/api/repositories/main/history?run_id={newer_id}")

    assert r.status_code == 200
    assert r.json()["reliability"]["test_x"]["test_case"]["pooled_attempts"] == 1, (
        "only the readable run pools"
    )
    assert viewed.status_code == 200, "viewing the newer run must not error the view"


def test_reliability_local_run_draws_over_remote_mainline() -> None:
    # A local run lives in a repo with no baseline. Its reliability must still
    # show, because the remote mount holds the baseline history to measure
    # against.
    remote = _make_baseline_repo()
    local = RunRepository(MemoryStore())
    local_run = make_recorded_run(
        make_round(assertions={"passed": True}),
        commit="local-tip",
        reliability_target=0.9,
    )
    local.save_run(local_run)
    app = create_app(
        {
            "local": MountedRepository(url="/x", repository=local, role="local"),
            "remote": MountedRepository(url="/y", repository=remote, role="remote"),
        }
    )
    with make_client(app) as client:
        body = client.get(
            f"/api/repositories/local/history?run_id={local_run.id}"
        ).json()
    case = body["reliability"]["test_x"]["test_case"]
    assert case["pooled_attempts"] == 4, (
        "mainline-only rate, the local run is not folded in"
    )
    assert case["diverged"] is False
    points = case["points"]
    assert len(points) == 5, "4 remote mainline points plus the local one"
    assert points[-1]["off_mainline"] is True, "the local run is the newest point"
    assert points[-1]["commit"] == "local-tip"
    assert [p["commit"] for p in points[:4]] == ["main-0", "main-1", "main-2", "main-3"]


def test_remote_view_never_borrows_the_local_mainline() -> None:
    """A fresh team remote holds PR refs and nothing promoted yet, while the
    developer once ran `promote --local`. The remote's cross-run views must be
    empty: the local mainline's numbers would read as the team's."""
    run = make_recorded_run(
        make_round(assertions={"passed": True}),
        commit="branch",
        reliability_target=0.9,
    )
    local = RunRepository(MemoryStore())
    local.save_run(run)
    local.move_ref("baseline", run.id, commit="local-main")
    remote = RunRepository(MemoryStore())
    remote.save_run(run)
    remote.move_ref("pr/1", run.id, pr=1)
    app = create_app(
        {
            "local": MountedRepository(url="/x", repository=local, role="local"),
            "remote": MountedRepository(url="/y", repository=remote, role="remote"),
        }
    )
    with make_client(app) as client:
        history = client.get("/api/repositories/remote/history").json()
        mainline = client.get(f"/api/repositories/remote/runs/{run.id}/mainline")
    assert history == EMPTY_HISTORY
    assert mainline.json() is None, "promoted locally only, so not on the mainline"


def test_remote_view_with_a_torn_baseline_reflog_is_422_not_the_local_numbers() -> None:
    """An unreadable mainline raises. The readable local baseline beside it is
    a developer's own promotion, never a substitute."""
    torn = make_corrupt_reflog_repository(ref="baseline")
    app = create_app(
        {
            "local": MountedRepository(
                url="/x", repository=_make_baseline_repo(), role="local"
            ),
            "remote": MountedRepository(url="/y", repository=torn.repo, role="remote"),
        }
    )
    with make_client(app) as client:
        r = client.get("/api/repositories/remote/history")
    assert r.status_code == 422
    assert "line 2" in r.json()["detail"], "the detail names the torn reflog line"


def test_score_history_tracks_the_baseline_history() -> None:
    """The trend answers "is this eval drifting?", which the pass and fail
    history cannot. Every run here passes its bar while the score slides toward
    it."""
    with make_repo_client(_make_scored_baseline_repo()) as client:
        trends = client.get("/api/repositories/main/history").json()["score_history"][
            "test_x"
        ]

    assert [t["score"] for t in trends] == ["quality"]
    assert list(trends[0]["cases"]) == ["test_case"]
    assert trends[0]["cases"]["test_case"]["bar"] == 0.4
    points = trends[0]["cases"]["test_case"]["points"]
    assert [p["value"] for p in points] == [0.9, 0.7, 0.5], (
        "the history runs oldest first"
    )
    assert [p["commit"] for p in points] == ["main-0", "main-1", "main-2"], (
        "points name the release, not the eval-time branch commit"
    )
    assert [p["pr"] for p in points] == [100, 101, 102]
    assert all(p["off_mainline"] is False for p in points), (
        "no point is off the mainline without a viewed run"
    )


def test_score_history_plots_a_re_promoted_run_once() -> None:
    """The trend reads the same history the pass-rate pools, so a rollback must
    not draw the restored run's score twice."""
    repo = RunRepository(MemoryStore())
    made: list[RunRecord] = []
    for quality in [0.9, 0.7]:
        run = make_recorded_run(
            make_round(scores={"quality": quality}),
            score_bars={"quality": 0.4},
        )
        repo.save_run(run)
        made.append(run)
    restored, reverted = made
    repo.move_ref("baseline", restored.id, commit="main-0")
    repo.move_ref("baseline", reverted.id, commit="main-1")
    repo.move_ref("baseline", restored.id, commit="main-2")

    with make_repo_client(repo) as client:
        trends = client.get("/api/repositories/main/history").json()["score_history"][
            "test_x"
        ]

    points = next(iter(trends[0]["cases"].values()))["points"]
    assert [p["value"] for p in points] == [0.7, 0.9]
    assert [p["commit"] for p in points] == ["main-1", "main-2"]


def test_run_mainline_returns_the_promote_commit_and_pr() -> None:
    """The run view reads this to show where a run landed on main. The mainline
    commit (from promote) differs from the run's own eval-time branch commit."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="branch-sha")
    repo.save_run(run)
    repo.move_ref("pr/7", run.id, commit="branch-sha", pr=7)
    promote(repo, "pr/7", commit="merge-sha")
    with make_repo_client(repo) as client:
        body = client.get(f"/api/repositories/main/runs/{run.id}/mainline").json()
    assert body["commit"] == "merge-sha", "the commit on main, not the branch commit"
    assert body["pr"] == 7
    assert "moved_at" in body


def test_run_mainline_carries_the_promoted_prs_title() -> None:
    """The mainline entry identifies which PR (and title) a run was promoted from,
    carried from the source ref through promote."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="branch-sha")
    repo.save_run(run)
    repo.move_ref("pr/7", run.id, pr=7, title="Add caching")
    promote(repo, "pr/7", commit="merge-sha")
    with make_repo_client(repo) as client:
        body = client.get(f"/api/repositories/main/runs/{run.id}/mainline").json()
    assert body["pr"] == 7
    assert body["title"] == "Add caching"


def test_run_mainline_names_the_newest_promotion() -> None:
    """A run reaches main more than once when a release is rolled back and
    promoted again. The view says where the run sits now, so the newest entry
    wins. The first match names the release it was rolled back from."""
    repo = RunRepository(MemoryStore())
    restored = make_recorded_run(make_round(), commit="branch-1")
    reverted = make_recorded_run(make_round(), commit="branch-2")
    repo.save_run(restored)
    repo.save_run(reverted)
    repo.move_ref("baseline", restored.id, commit="merge-1", pr=1)
    repo.move_ref("baseline", reverted.id, commit="merge-2", pr=2)
    repo.move_ref("baseline", restored.id, commit="merge-3", pr=3)

    with make_repo_client(repo) as client:
        body = client.get(f"/api/repositories/main/runs/{restored.id}/mainline").json()

    assert body["commit"] == "merge-3"
    assert body["pr"] == 3


@pytest.mark.parametrize(
    "run_id", ["%00", "..%5C..%5Csecret", "01KZ5HPEFMEBFBF39RT2SAJSJV%00.json"]
)
def test_run_mainline_rejects_a_malformed_run_id(run_id: str) -> None:
    """This endpoint only compares against reflog entries, so a malformed id
    used to answer `200 null`. That reads as a run nobody promoted, leaving the
    client unable to tell that it sent nonsense. The sibling run endpoints
    answer 400, and this one has to agree."""
    repo = RunRepository(MemoryStore())
    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{run_id}/mainline")
    assert r.status_code == 400
    assert "invalid identifier" in r.json()["detail"]


def test_run_mainline_is_null_when_the_run_was_never_promoted() -> None:
    repo = RunRepository(MemoryStore())
    promoted = make_recorded_run(make_round(), commit="c")
    other = make_recorded_run(make_round(), commit="c")
    repo.save_run(promoted)
    repo.save_run(other)
    repo.move_ref("baseline", promoted.id, commit="m")
    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{other.id}/mainline")
    assert r.json() is None


def test_run_mainline_resolves_a_local_run_against_the_remote_baseline() -> None:
    """A local run viewed against the remote mainline. The run keeps its id when
    promoted, so its mainline entry sits on the remote's baseline reflog."""
    remote = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="branch")
    remote.save_run(run)
    remote.move_ref("pr/3", run.id, pr=3)
    promote(remote, "pr/3", commit="merge-sha")
    local = RunRepository(MemoryStore())
    local.save_run(run)  # same immutable run id, no baseline of its own
    app = create_app(
        {
            "local": MountedRepository(url="/x", repository=local, role="local"),
            "remote": MountedRepository(url="/y", repository=remote, role="remote"),
        }
    )
    with make_client(app) as client:
        body = client.get(f"/api/repositories/local/runs/{run.id}/mainline").json()
    assert body["commit"] == "merge-sha"
    assert body["pr"] == 3
