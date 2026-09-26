"""The dashboard's report download: the single-file report served as an
attachment, for a run the user is looking at."""

import json
import re
from pathlib import Path

import pytest

import evaltrack.ui.report as report_module
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories import RunRepository
from evaltrack.ui.app import MountedRepository, create_app

from ..factories import make_round
from ..fakes import MemoryStore, RaisingStore
from .conftest import (
    make_client,
    make_recorded_run,
    make_repo_app,
    make_repo_client,
)

TEMPLATE = (
    "<!doctype html><title>evaltrack report</title>"
    '<script type="application/json" id="evaltrack-data"></script>'
)


@pytest.fixture(autouse=True)
def template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the renderer at a temp template, so these tests run whether or not
    this checkout has a frontend build."""
    path = tmp_path / "report.html"
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", path)
    return path


def embedded_json(html: str) -> dict[str, object]:
    match = re.search(
        r'<script type="application/json" id="evaltrack-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, "the page carries the data element"
    return json.loads(match.group(1))


def test_report_is_an_html_attachment_holding_the_run() -> None:
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("pr/3", run.id, pr=3)

    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{run.id}/report?via=pr/3")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert (
        r.headers["content-disposition"]
        == f'attachment; filename="evaltrack-report-{run.id}.html"'
    )
    data = embedded_json(r.text)
    embedded = data["run"]
    assert isinstance(embedded, dict) and embedded["id"] == run.id
    assert data["via"] == "pr/3"
    assert data["against"] is None
    refs = data["refs"]
    assert isinstance(refs, list) and [r["name"] for r in refs] == ["pr/3"]


def test_comparison_report_takes_the_base_from_another_mount() -> None:
    """A local run compared against the remote baseline is the dashboard's
    everyday comparison, and the report has to reach both mounts for it."""
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
        r = client.get(
            f"/api/repositories/local/runs/{mine.id}/report",
            params={
                "against": base.id,
                "against_slug": "remote",
                "against_via": "baseline",
            },
        )

    assert r.status_code == 200
    assert (
        r.headers["content-disposition"]
        == f'attachment; filename="evaltrack-report-{base.id}-to-{mine.id}.html"'
    )
    data = embedded_json(r.text)
    against = data["against"]
    assert isinstance(against, dict) and against["id"] == base.id
    assert data["against_via"] == "baseline"


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
    history = embedded_json(r.text)["history"]
    assert isinstance(history, dict)
    assert history["reliability"]["test_x"]["test_case"]["pooled_runs"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?against=01J9Z3QW2KJ5H8VN4TQY7B6MDC",
    ],
    ids=["the run", "the comparison run"],
)
def test_report_of_a_missing_run_404s(query: str) -> None:
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    run_id = run.id if query else "01J9Z3QW2KJ5H8VN4TQY7B6MDC"

    with make_repo_client(repo) as client:
        r = client.get(f"/api/repositories/main/runs/{run_id}/report{query}")

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
    data = embedded_json(r.text)
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
    data = embedded_json(r.text)
    assert data["history"] == {"reliability": {}, "score_history": {}}
    error = data["history_error"]
    assert isinstance(error, str) and "expired login" in error
