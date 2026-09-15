"""The single-file report: what it embeds, and how it keeps a run's content
from breaking out of the page."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

import evaltrack.ui.report as report_module
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import ReportData
from evaltrack.ui.report import collect_report_data, render_report

from ..factories import make_attempt, make_round
from ..fakes import MemoryStore
from .conftest import make_recorded_run

# What a built page carries: the element the CLI fills, inside markup the
# browser needs. The build's real output is larger, and the tests only need
# the slot.
TEMPLATE = (
    "<!doctype html><html><head><title>evaltrack report</title></head><body>"
    '<div id="root"></div>'
    '<script type="application/json" id="evaltrack-data"></script>'
    "<script>window.rendered = true;</script></body></html>"
)

GENERATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the renderer at a temp template, so the tests run whether or not
    this checkout has a frontend build."""
    path = tmp_path / "report.html"
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", path)
    return path


def make_data(**overrides: object) -> ReportData:
    run = make_recorded_run(make_round(), commit="c0")
    fields: dict[str, object] = {
        "run": run,
        "generated_at": GENERATED_AT,
        "generated_by": "0.0.0",
    }
    fields.update(overrides)
    return ReportData.model_validate(fields)


def embedded_json(html: str) -> dict[str, object]:
    """The report's data as the page reads it: the one JSON element's text."""
    matches = re.findall(
        r'<script type="application/json" id="evaltrack-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert len(matches) == 1, "the page carries exactly one data element"
    return json.loads(matches[0])


def recorded_output(embedded: dict[str, object]) -> object:
    """The output of the one case the fixture run records."""
    run = embedded["run"]
    assert isinstance(run, dict)
    return run["tests"]["test_x"]["cases"]["test_case"]["attempts"][0]["output"]


def test_the_page_carries_the_run_as_the_api_serves_it(template: Path) -> None:
    data = make_data()

    html = render_report(data)

    embedded = embedded_json(html)
    assert embedded["run"] == json.loads(data.run.model_dump_json())
    assert embedded["against"] is None
    assert embedded["generated_at"] == "2026-01-02T03:04:05Z"
    assert html.startswith("<!doctype html>"), "the template around the data survives"


def test_a_comparison_embeds_both_runs(template: Path) -> None:
    against = make_recorded_run(make_round(assertions={"passed": False}), commit="c1")
    data = make_data(against=against, against_via="baseline", via="pr/7")

    embedded = embedded_json(render_report(data))

    assert embedded["run"] == json.loads(data.run.model_dump_json())
    assert embedded["against"] == json.loads(against.model_dump_json())
    assert (embedded["via"], embedded["against_via"]) == ("pr/7", "baseline")


def test_an_output_cannot_close_the_data_element(template: Path) -> None:
    """A model output is whatever the task returned. The parser ends a script
    element at the first `</script`, whatever the element's type, so one in
    an output would turn the rest of the run into markup and script."""
    hostile = '</script><script>alert("x")</script>&amp;\u2028\u2029'
    run = make_recorded_run(
        make_round(attempts=[make_attempt(output=hostile)]), commit="c0"
    )

    html = render_report(make_data(run=run))

    _, _, tail = html.partition('id="evaltrack-data">')
    body, _, rest = tail.partition("</script>")
    assert "<" not in body and ">" not in body and "&" not in body
    assert "\u2028" not in body and "\u2029" not in body
    assert "alert" in body, "the output is still in the data"
    assert rest.startswith("<script>window.rendered"), (
        "the template's own script follows"
    )
    # The escapes decode back to the text, so the page renders what was recorded.
    embedded = embedded_json(html)
    assert recorded_output(embedded) == hostile


def test_the_page_names_no_remote_asset(template: Path) -> None:
    html = render_report(make_data())
    assert not re.search(r'(src|href)="(https?:)?//', html)


def test_a_missing_template_names_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", tmp_path / "missing.html")

    with pytest.raises(FileNotFoundError, match="frontend_build"):
        render_report(make_data())


def test_a_template_without_a_slot_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page from another build has nowhere to put the run, and a report
    with no run in it must not be written as if it had one."""
    path = tmp_path / "report.html"
    path.write_text("<!doctype html><title>other</title>", encoding="utf-8")
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", path)

    with pytest.raises(ValueError, match="data slot"):
        render_report(make_data())


def test_collected_data_drops_raw_results_and_finds_the_mainline() -> None:
    """The page renders from `cases`, so the runner's own result objects only
    add weight. The mainline entry is the promote that carried the run."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("baseline", run.id, commit="main-0", pr=3, title="Land it")
    assert run.tests["test_x"].raw_results, "the fixture records raw results"

    data = collect_report_data(repo, run, via="baseline")

    assert data.run.tests["test_x"].raw_results == []
    assert data.mainline is not None
    assert (data.mainline.commit, data.mainline.pr) == ("main-0", 3)
    assert data.via == "baseline"
    assert data.against is None


def test_collected_history_is_measured_over_the_repository_baseline() -> None:
    """A single-run report carries the reliability the dashboard would show
    for it: the run over its repository's promoted history."""
    repo = RunRepository(MemoryStore())
    for i, ok in enumerate([True, False, True]):
        promoted = make_recorded_run(
            make_round(assertions={"passed": ok}),
            commit=f"c{i}",
            reliability_target=0.9,
        )
        repo.save_run(promoted)
        repo.move_ref("baseline", promoted.id, commit=f"main-{i}")
    viewed = make_recorded_run(make_round(), commit="pr", reliability_target=0.9)
    repo.save_run(viewed)

    data = collect_report_data(repo, viewed)

    reliability = data.history.reliability["test_x"]["test_case"]
    assert reliability.pooled_runs == 3
    assert [p.off_mainline for p in reliability.points][-1] is True
    assert data.mainline is None


def test_a_comparison_reads_no_history() -> None:
    """The comparison view does not show it, and it costs a run body per
    mainline entry."""
    repo = RunRepository(MemoryStore())
    base = make_recorded_run(make_round(), commit="c0")
    repo.save_run(base)
    repo.move_ref("baseline", base.id, commit="main-0")
    viewed = make_recorded_run(make_round(), commit="c1")
    repo.save_run(viewed)

    data = collect_report_data(repo, viewed, against=base, against_via="baseline")

    assert data.history.reliability == {}
    assert data.against is not None
    assert data.against.id == base.id
