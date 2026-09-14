"""The dashboard's read endpoints: the repository listing, runs, refs, reflogs,
the project config, and how paging and multi-mount routing behave."""

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from evaltrack.core.run_record import RUN_SCHEMA_VERSION
from evaltrack.repositories import RunRepository
from evaltrack.ui import MountedRepository, create_app

from ..factories import make_crash_round, make_round
from ..fakes import MemoryStore
from .conftest import (
    make_client,
    make_corrupt_reflog_repository,
    make_recorded_run,
    make_repo_app,
    make_repo_client,
)


def test_list_repositories(client_factory: TestClient) -> None:
    data = client_factory.get("/api/repositories").json()
    assert data == [{"slug": "main", "url": "/x", "role": "local"}]


def test_list_runs_returns_summaries(client_factory: TestClient) -> None:
    """The listing serves the summary projection, never the run bodies. A body
    carries every attempt's raw report, which is what the projection exists to
    keep out of a list view."""
    r = client_factory.get("/api/repositories/main/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    assert "tests" not in data[0], "no per-test payload rides along"
    assert data[0]["tests_total"] == 1, "the counts stand in for it"


def test_list_refs_lists_a_ref_whose_reflog_is_corrupt() -> None:
    """The refs listing used to 500 on one torn reflog line, hiding every other
    ref. The broken ref must be listed, so the dashboard can show it and offer
    the delete that fixes it, and it must carry the error, so the dashboard can
    say the history is unreadable rather than empty."""
    setup = make_corrupt_reflog_repository()
    with make_repo_client(setup.repo) as client:
        r = client.get("/api/repositories/main/refs")
    assert r.status_code == 200
    (listed,) = r.json()
    assert listed["name"] == "pr/1"
    assert listed["tip"] is None
    assert "line 2" in listed["error"], "the error names the torn reflog line"


def test_refs_carry_their_tip_runs_outcome() -> None:
    """A ref list must show pass and fail without a read of each ref, so the tip
    run's counts ride along with the ref."""
    repo = RunRepository(MemoryStore())
    green = make_recorded_run(outcome="passed")
    red = make_recorded_run(make_round(assertions={"passed": False}), outcome="failed")
    repo.save_run(green)
    repo.save_run(red)
    repo.move_ref("baseline", green.id, commit="c0")
    repo.move_ref("pr/7", red.id, commit="c1", pr=7)

    with make_repo_client(repo) as client:
        refs = {
            ref["name"]: ref for ref in client.get("/api/repositories/main/refs").json()
        }
    assert refs["baseline"]["tip_run"]["tests_failed"] == 0
    assert refs["pr/7"]["tip_run"]["id"] == red.id
    assert refs["pr/7"]["tip_run"]["tests_failed"] == 1


def test_reflog_entries_carry_their_runs_outcome() -> None:
    """Same reason as the ref listing. The mainline history is a list of run
    rows, so each entry carries its run's counts."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(assertions={"passed": False}), outcome="failed")
    repo.save_run(run)
    repo.move_ref("baseline", run.id, commit="c0")

    with make_repo_client(repo) as client:
        entries = client.get("/api/repositories/main/reflogs/baseline").json()
    assert entries[0]["run"]["tests_failed"] == 1
    assert entries[0]["run_id"] == run.id, "the entry's own fields are intact"


def test_ref_tip_run_is_null_when_the_run_is_gone() -> None:
    """A ref can outlive its run (a torn delete). The listing must still serve,
    with no outcome rather than an error."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run()
    repo.save_run(run)
    repo.move_ref("pr/9", run.id, pr=9)
    repo.delete_run_unchecked(run.id)

    with make_repo_client(repo) as client:
        refs = client.get("/api/repositories/main/refs").json()
    assert refs[0]["tip"]["run_id"] == run.id
    assert refs[0]["tip_run"] is None


def test_get_ref_with_a_corrupt_reflog_names_the_line() -> None:
    """A request for the one ref is where the corruption surfaces. The answer is
    not a 500, and it carries the storage key and line number the operator
    needs."""
    setup = make_corrupt_reflog_repository()
    with make_repo_client(setup.repo) as client:
        ref = client.get("/api/repositories/main/refs/pr/1")
        log = client.get("/api/repositories/main/reflogs/pr/1")
    assert ref.status_code == 422, "the ref exists, so it is not a 404"
    assert "line 2" in ref.json()["detail"], "the detail names the torn reflog line"
    assert log.status_code == 422


def test_list_runs_respects_limit(client_factory: TestClient) -> None:
    r = client_factory.get("/api/repositories/main/runs?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_runs_pagination_via_offset(client_factory: TestClient) -> None:
    """limit + offset together let the dashboard's `Load more` button fetch
    the next page without re-reading the first one."""
    page1 = client_factory.get("/api/repositories/main/runs?limit=2&offset=0").json()
    page2 = client_factory.get("/api/repositories/main/runs?limit=2&offset=2").json()
    assert len(page1) == 2
    assert len(page2) == 1, "3 runs total leave one on the second page"
    # No overlap between pages.
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


@pytest.mark.parametrize(
    "path", ["/api/repositories/main/runs", "/api/repositories/main/reflogs/baseline"]
)
def test_page_size_past_the_ceiling_rejected(
    client_factory: TestClient, path: str
) -> None:
    """A page costs a store read per item, and reads take no Origin check, so any
    page the user visits can ask for a huge one. Both paginated endpoints need
    the ceiling."""
    r = client_factory.get(f"{path}?limit=100000")
    assert r.status_code == 422, "an oversized page is refused before any store read"


def test_get_run_returns_full_payload(client_factory: TestClient) -> None:
    summaries = client_factory.get("/api/repositories/main/runs").json()
    target_id = summaries[0]["id"]
    r = client_factory.get(f"/api/repositories/main/runs/{target_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == target_id
    assert "tests" in data


def test_get_run_missing_404(client_factory: TestClient) -> None:
    r = client_factory.get("/api/repositories/main/runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC")
    assert r.status_code == 404


def test_get_run_strips_raw_results(client_factory: TestClient) -> None:
    summaries = client_factory.get("/api/repositories/main/runs").json()
    target_id = summaries[0]["id"]
    data = client_factory.get(f"/api/repositories/main/runs/{target_id}").json()
    evaluated = [t for t in data["tests"].values() if t["marker"] is not None]
    assert evaluated, "sanity: the fixture run carries evals"
    assert all(t["raw_results"] == [] for t in evaluated), (
        "the view omits the heavy raw reports"
    )
    assert all(t["cases"] for t in evaluated), "the structured cases still stand"


def test_get_run_carries_the_crashed_attempt() -> None:
    """The run view strips the raw reports. A crashed attempt must travel in the
    structured cases, or the dashboard cannot show it at all."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_crash_round())
    repo.save_run(run)
    with make_repo_client(repo) as client:
        data = client.get(f"/api/repositories/main/runs/{run.id}").json()
    case = data["tests"]["test_x"]["cases"]["test_case"]
    attempt = case["attempts"][0]
    assert attempt["outcome"] == "errored"


def test_download_run_includes_raw_results(client_factory: TestClient) -> None:
    summaries = client_factory.get("/api/repositories/main/runs").json()
    target_id = summaries[0]["id"]
    r = client_factory.get(f"/api/repositories/main/runs/{target_id}/download")
    assert r.status_code == 200
    assert (
        r.headers["content-disposition"] == f'attachment; filename="{target_id}.json"'
    )
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    evaluated = [t for t in data["tests"].values() if t["marker"] is not None]
    assert any(t["raw_results"] for t in evaluated), (
        "the download keeps the raw reports"
    )


def test_download_run_missing_404(client_factory: TestClient) -> None:
    r = client_factory.get(
        "/api/repositories/main/runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC/download"
    )
    assert r.status_code == 404


@dataclass(frozen=True)
class _StoredRun:
    """A repo holding one saved run, with the store exposed so a test can
    rewrite the stored bytes underneath the repository."""

    repo: RunRepository
    store: MemoryStore
    run_id: str


def _make_stored_run_setup() -> _StoredRun:
    store = MemoryStore()
    repo = RunRepository(store)
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    return _StoredRun(repo, store, run.id)


def test_run_in_another_stored_format_is_a_clean_501_not_the_corrupt_422() -> None:
    """A run stamped with a newer schema is refused even when its shape still
    parses. It answers 501, not the 422 a damaged body gets: the run is whole,
    and it is this server that lacks the format. A client that reads 422 as
    "unreadable bytes, delete it" would destroy history it could still read
    elsewhere. Not a 500 either, which reads as a server fault."""
    saved = _make_stored_run_setup()
    stored = json.loads(saved.store.read(f"runs/{saved.run_id}.json"))
    stored["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    saved.store.write(json.dumps(stored).encode(), f"runs/{saved.run_id}.json")

    with make_repo_client(saved.repo) as client:
        r = client.get(f"/api/repositories/main/runs/{saved.run_id}")
        d = client.get(f"/api/repositories/main/runs/{saved.run_id}/download")

    assert r.status_code == 501, "a newer format is not the corrupt-body 422"
    detail = r.json()["detail"]
    assert "this evaltrack reads" in detail
    assert "Delete" not in detail, "an intact run must never be offered a delete"
    assert d.status_code == 501, "the download refuses the newer format too"


@pytest.mark.parametrize(
    "path",
    [
        "runs",
        "history",
        "runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC/mainline",
    ],
)
def test_unknown_repository_slug_404(client_factory: TestClient, path: str) -> None:
    r = client_factory.get(f"/api/repositories/missing/{path}")
    assert r.status_code == 404


def test_list_refs_classifies_by_stored_pr(client_factory: TestClient) -> None:
    """`baseline` is the mainline by its reserved name, even though its promote
    recorded a PR number. Any other ref carrying one is a pull request."""
    r = client_factory.get("/api/repositories/main/refs")
    assert r.status_code == 200
    assert [(x["name"], x["kind"], x["tip"]["pr"]) for x in r.json()] == [
        ("baseline", "baseline", 1),
        ("pr/42", "pr", 42),
    ]


def test_list_refs_carries_each_refs_pointer(client_factory: TestClient) -> None:
    """The sidebar renders its rows from the listing, so the listing carries each
    ref's tip pointer. A separate fetch per ref cost a reflog read per ref on
    every dashboard open, and the listing already read that data."""
    listed = {
        x["name"]: x for x in client_factory.get("/api/repositories/main/refs").json()
    }
    for name, kind in [("baseline", "baseline"), ("pr/42", "pr")]:
        tip = client_factory.get(f"/api/repositories/main/refs/{name}").json()
        assert listed[name] == {
            "name": name,
            "tip": tip,
            "kind": kind,
            "tip_run": listed[name]["tip_run"],
            "error": None,
        }
        assert listed[name]["tip_run"]["id"] == tip["run_id"], (
            f"the {name} row carries its own tip's run"
        )
    assert listed["pr/42"]["tip"]["commit"] == "c1"
    assert listed["pr/42"]["tip"]["run_id"]


def test_list_refs_uses_stored_pr_not_the_ref_name() -> None:
    """The ref's stored PR number drives the classification, not its name. A
    freely-named ref with a PR number is a pull request, and a `pr/`-named ref
    without one is a plain ref."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("alice/login-fix", run.id, pr=7)  # PR number, unconventional name
    repo.move_ref("pr/99", run.id)  # PR-looking name, no stored number
    with make_repo_client(repo) as client:
        body = client.get("/api/repositories/main/refs").json()
    by_name = {r["name"]: r for r in body}
    alice = by_name["alice/login-fix"]
    assert (alice["kind"], alice["tip"]["pr"]) == ("pr", 7)
    assert (by_name["pr/99"]["kind"], by_name["pr/99"]["tip"]["pr"]) == ("other", None)


def test_list_refs_carries_the_tip_entrys_title() -> None:
    """A PR ref's title (its identity in the dashboard) comes from the tip reflog
    entry, populated like the PR number."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("pr/42", run.id, pr=42, title="Add caching")
    with make_repo_client(repo) as client:
        body = client.get("/api/repositories/main/refs").json()
    assert [
        (x["name"], x["kind"], x["tip"]["pr"], x["tip"]["title"]) for x in body
    ] == [("pr/42", "pr", 42, "Add caching")]


def test_list_refs_keeps_a_ref_that_points_at_nothing() -> None:
    """A zero-byte reflog (a crash between reflog creation and its first append)
    leaves a ref with no history. It must still list, with no pointer, because a
    delete from the dashboard is the only way to clean it up."""
    store = MemoryStore()
    repo = RunRepository(store)
    store.write(b"", "refs/pr/13.log.jsonl")
    with make_repo_client(repo) as client:
        body = client.get("/api/repositories/main/refs").json()
    assert body == [
        {"name": "pr/13", "tip": None, "kind": "other", "tip_run": None, "error": None}
    ]


def test_list_refs_keeps_a_foreign_key_under_refs() -> None:
    """A key under `refs/` that is not a valid ref name cannot be read as a ref. It
    lists with no tip and the naming error, like a torn reflog, so the refs
    beside it still list."""
    store = MemoryStore()
    repo = RunRepository(store)
    store.write(b"{}\n", "refs/Notes.log.jsonl")
    with make_repo_client(repo) as client:
        (listed,) = client.get("/api/repositories/main/refs").json()
    assert listed["name"] == "Notes"
    assert listed["tip"] is None
    assert listed["error"], "the naming error rides along"


def test_config_defaults_to_no_pr_url_template() -> None:
    with make_repo_client(RunRepository(MemoryStore())) as client:
        assert client.get("/api/config").json() == {"pr_url_template": None}


def test_config_exposes_the_pr_url_template() -> None:
    """The frontend fetches this project-level value to link PR numbers to their
    host."""
    template = "https://github.com/OWNER/REPO/pull/{pr}"
    app = make_repo_app(RunRepository(MemoryStore()), pr_url_template=template)
    with make_client(app) as client:
        assert client.get("/api/config").json() == {"pr_url_template": template}


def test_create_app_checks_the_pr_url_template() -> None:
    """A library caller reaches the dashboard without the config file, so this
    code must check the template too. The rules themselves are covered where the
    type is defined."""
    with pytest.raises(ValidationError, match="http"):
        make_repo_app(
            RunRepository(MemoryStore()),
            pr_url_template="javascript:alert(1)//{pr}",
        )


@pytest.mark.parametrize(("name", "commit"), [("baseline", "c0"), ("pr/42", "c1")])
def test_get_ref(client_factory: TestClient, name: str, commit: str) -> None:
    """Covers both flat names and namespaced ones. The `pr/42` case exercises
    the `:path` converter on `{name:path}` so a refactor to plain `{name}`
    would fail here instead of silently dropping `/`-containing refs."""
    r = client_factory.get(f"/api/repositories/main/refs/{name}")
    assert r.status_code == 200
    body = r.json()
    assert "run_id" in body
    assert "moved_at" in body
    assert body["commit"] == commit, "the ref carries its tip entry's commit"


def test_get_ref_missing_404(client_factory: TestClient) -> None:
    r = client_factory.get("/api/repositories/main/refs/nope")
    assert r.status_code == 404


def test_traversal_ref_name_rejected_400(client_factory: TestClient) -> None:
    """A `..` traversal segment is a malformed name, not a missing resource. It
    is rejected before any storage read, so nobody can use the dashboard to read
    a file outside the repository root. See the InvalidIdentifierError handler in
    app.py.
    """
    r = client_factory.get("/api/repositories/main/refs/..%2F..%2Fsecret")
    assert r.status_code == 400
    assert "invalid identifier" in r.json()["detail"]


def test_get_reflog(client_factory: TestClient) -> None:
    r = client_factory.get("/api/repositories/main/reflogs/pr/42")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["commit"] == "c1"


def test_get_reflog_pagination_newest_first(
    client_factory: TestClient, populated_repository: RunRepository
) -> None:
    """limit + offset page the reflog newest-first from the current tip, so the
    dashboard's history view shows the recent past on page one and "load more"
    walks back toward the oldest entry. This guards a regression. A page from
    the oldest end rendered the oldest entries as recent history, and never
    showed the actual tip once the log outgrew one page."""
    # Grow the promoted history well past one page (the fixture logged c0).
    # Cycle the target run, and start away from the fixture's tip
    # (summaries[0]). move_ref is idempotent, so a re-point at the current run
    # appends nothing.
    summaries = list(populated_repository.list_runs())
    for i in range(7):
        populated_repository.move_ref(
            "baseline", summaries[(i + 1) % 3].id, commit=f"m{i}"
        )

    def get_page(limit: int, offset: int) -> list[dict[str, object]]:
        r = client_factory.get(
            f"/api/repositories/main/reflogs/baseline?limit={limit}&offset={offset}"
        )
        assert r.status_code == 200, f"the page at offset {offset} must serve"
        return r.json()

    pages = [get_page(3, 0), get_page(3, 3), get_page(3, 6)]
    assert [len(p) for p in pages] == [3, 3, 2]
    baseline = populated_repository.get_ref("baseline")
    assert baseline is not None
    assert pages[0][0]["run_id"] == baseline.run_id, (
        "page one starts at the ref's current tip"
    )
    commits = [e["commit"] for p in pages for e in p]
    assert commits == ["m6", "m5", "m4", "m3", "m2", "m1", "m0", "c0"], (
        "the pages walk back in time and cover every entry exactly once"
    )
    assert get_page(3, 8) == [], "past the end is empty, so a pager knows it's done"


def test_create_app_rejects_empty_repositories() -> None:
    with pytest.raises(ValueError, match="at least one"):
        create_app({})


def test_multi_repository_mount() -> None:
    """The same app can serve more than one repository, each under its own slug."""
    a = RunRepository(MemoryStore())
    b = RunRepository(MemoryStore())
    a.save_run(make_recorded_run(test="t"))

    with make_client(
        create_app(
            {
                "a": MountedRepository(url="/a", repository=a, role="local"),
                "b": MountedRepository(url="/b", repository=b, role="remote"),
            }
        )
    ) as client:
        repos = client.get("/api/repositories").json()
        assert [r["slug"] for r in repos] == ["a", "b"]
        assert len(client.get("/api/repositories/a/runs").json()) == 1
        assert len(client.get("/api/repositories/b/runs").json()) == 0, (
            "one mount's runs must not leak into another"
        )


def test_repositories_listed_local_first_then_remote() -> None:
    """The listing orders by role (local, then remote) so the dashboard can
    render the sections in a stable, meaningful order regardless of slug."""
    local = RunRepository(MemoryStore())
    remote = RunRepository(MemoryStore())
    with make_client(
        create_app(
            {
                # Deliberately insert remote first and pick slugs that would sort
                # the other way, to prove role drives the order, not slug or
                # insertion.
                "z-remote": MountedRepository(
                    url="azure://acct/evals", repository=remote, role="remote"
                ),
                "a-local": MountedRepository(
                    url="./.evaltrack", repository=local, role="local"
                ),
            }
        )
    ) as client:
        repos = client.get("/api/repositories").json()
        assert [(r["slug"], r["role"]) for r in repos] == [
            ("a-local", "local"),
            ("z-remote", "remote"),
        ]
