"""The dashboard's report download: the single-file report served as an
attachment, for a run the user is looking at."""

from pathlib import Path

import pytest

import evaltrack.report.page as report_module
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories import RunRepository
from evaltrack.ui.app import MountedRepository, create_app

from ..factories import make_round
from ..fakes import MemoryStore, RaisingStore
from ..report_support import embedded_report_json, embedded_run
from .conftest import (
    make_client,
    make_recorded_run,
    make_repo_app,
    make_repo_client,
)

pytestmark = pytest.mark.usefixtures("report_template")


def test_report_is_an_html_attachment_holding_the_run() -> None:
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("pr/3", run.id, pr=3)

    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{run.id}/report?via_ref=pr/3")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert (
        r.headers["content-disposition"]
        == f'attachment; filename="evaltrack-report-{run.id}.html"'
    )
    data = embedded_report_json(r.text)
    assert embedded_run(data)["id"] == run.id
    assert data["via_ref"] == "pr/3"
    assert data["baseline"] is None
    refs = data["refs"]
    assert isinstance(refs, list) and [r["name"] for r in refs] == ["pr/3"]


def test_report_of_a_local_run_carries_the_remote_baseline() -> None:
    """A local run compared against the remote baseline is the dashboard's
    everyday comparison, so the report it hands out carries that run."""
    local, remote = RunRepository(MemoryStore()), RunRepository(MemoryStore())
    base = make_recorded_run(make_round(), commit="c0")
    remote.save_run(base)
    remote.move_ref("baseline", base.id)
    mine = make_recorded_run(make_round(), commit="wip")
    local.save_run(mine)
    app = create_app(
        {
            "local": MountedRepository(url="/l", repository=local, role="local"),
            "remote": MountedRepository(url="/r", repository=remote, role="remote"),
        }
    )

    with make_client(app) as client:
        r = client.get(f"/api/repositories/local/runs/{mine.id}/report")

    assert r.status_code == 200
    data = embedded_report_json(r.text)
    baseline = data["baseline"]
    assert isinstance(baseline, dict) and baseline["id"] == base.id


def test_single_run_report_measures_history_over_the_remote() -> None:
    """The dashboard draws a local run over the remote's mainline, and the
    report it hands out shows the same."""
    local, remote = RunRepository(MemoryStore()), RunRepository(MemoryStore())
    promoted = make_recorded_run(make_round(), commit="c0", reliability_target=0.9)
    remote.save_run(promoted)
    remote.move_ref("baseline", promoted.id, commit="main-0")
    mine = make_recorded_run(make_round(), commit="wip", reliability_target=0.9)
    local.save_run(mine)
    app = create_app(
        {
            "local": MountedRepository(url="/l", repository=local, role="local"),
            "remote": MountedRepository(url="/r", repository=remote, role="remote"),
        }
    )

    with make_client(app) as client:
        r = client.get(f"/api/repositories/local/runs/{mine.id}/report")

    assert r.status_code == 200
    history = embedded_report_json(r.text)["history"]
    assert isinstance(history, dict)
    assert history["reliability"]["test_x"]["test_case"]["pooled_runs"] == 1


def test_report_of_a_missing_run_404s() -> None:
    repo = RunRepository(MemoryStore())

    with make_repo_client(repo) as client:
        r = client.get("/api/repositories/main/runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC/report")

    assert r.status_code == 404


def test_report_without_a_built_template_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable install before `just frontend_build`: the dashboard is up
    on its fallback page, and the report says what to run rather than crash."""
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", tmp_path / "missing.html")
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{run.id}/report")

    assert r.status_code == 503, "an unbuilt template is not a request fault"
    assert "frontend_build" in r.json()["detail"]


def test_report_carries_the_dashboard_pr_link_template() -> None:
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    app = make_repo_app(repo, pr_url_template="https://example.test/pull/{pr}")

    with make_client(app) as client:
        r = client.get(f"/api/repositories/main/runs/{run.id}/report")

    assert r.status_code == 200
    data = embedded_report_json(r.text)
    assert data["pr_url_template"] == "https://example.test/pull/{pr}"


def test_report_of_a_local_run_survives_an_unreachable_remote() -> None:
    """The dashboard drops the history column when the remote is down, and
    the report it hands out does the same rather than failing."""
    local = RunRepository(MemoryStore())
    mine = make_recorded_run(make_round(), commit="wip")
    local.save_run(mine)
    down = RunRepository(RaisingStore(RepositoryUnavailableError("expired login")))
    app = create_app(
        {
            "local": MountedRepository(url="/l", repository=local, role="local"),
            "remote": MountedRepository(url="/r", repository=down, role="remote"),
        }
    )

    with make_client(app) as client:
        r = client.get(f"/api/repositories/local/runs/{mine.id}/report")

    assert r.status_code == 200, "the run is what the report is for"
    data = embedded_report_json(r.text)
    assert data["history"] == {"reliability": {}, "score_history": {}}
    error = data["mainline_error"]
    assert isinstance(error, str) and "expired login" in error


def test_report_from_a_dashboard_with_no_remote_says_why_it_has_no_history() -> None:
    """A reader of the file never saw the dashboard, so the page has to say
    that there was no mainline to read."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

    with make_client(make_repo_app(repo, role="local")) as client:
        r = client.get(f"/api/repositories/main/runs/{run.id}/report")

    assert r.status_code == 200
    error = embedded_report_json(r.text)["mainline_error"]
    assert isinstance(error, str) and "remote" in error
