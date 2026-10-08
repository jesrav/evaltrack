"""The report download of the dashboard. It serves the single-file report of a run as an
attachment."""

from pathlib import Path

import pytest

import evaltrack.report.page as report_module
from evaltrack.repositories import RunRepository

from ..factories import make_round
from ..fakes import MemoryStore
from ..report_support import embedded_report_json, embedded_run
from .conftest import (
    make_client,
    make_recorded_run,
    make_repo_app,
    make_repo_client,
)

pytestmark = pytest.mark.usefixtures("report_template")


def test_report_is_an_html_attachment_holding_the_run() -> None:
    """The route serves the page as a download, with the run it was asked for and
    the ref it was reached by."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

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


def test_report_of_a_missing_run_404s() -> None:
    repo = RunRepository(MemoryStore())

    with make_repo_client(repo) as client:
        r = client.get("/api/repositories/main/runs/01J9Z3QW2KJ5H8VN4TQY7B6MDC/report")

    assert r.status_code == 404


def test_report_without_a_built_template_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable install has no report template until `just frontend_build` has run.
    The route answers 503 and names that command."""
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
