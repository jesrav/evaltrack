"""The single-file report: what it embeds, and how it keeps a run's content
from breaking out of the page."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

import evaltrack.ui.report as report_module
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    RunRecord,
    dump_run_json,
    parse_run_json,
)
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import Mainline, NamedRun, ReportData
from evaltrack.ui.report import collect_report_data, render_report

from ..factories import make_attempt, make_round
from ..fakes import MemoryStore, RaisingStore
from ..report_support import embedded_report_json, named_run
from .conftest import make_recorded_run

GENERATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def make_data(
    run: RunRecord | None = None,
    *,
    via: str | None = None,
    baseline: RunRecord | None = None,
) -> ReportData:
    return ReportData(
        run=NamedRun(run=run or make_recorded_run(make_round(), commit="c0"), via=via),
        baseline=baseline,
        generated_at=GENERATED_AT,
        generated_by="0.0.0",
    )


def recorded_output(embedded: dict[str, object]) -> object:
    """The output of the one case the fixture run records."""
    run = named_run(embedded)["run"]
    return run["tests"]["test_x"]["cases"]["test_case"]["attempts"][0]["output"]


def test_the_page_carries_the_run_as_the_api_serves_it(report_template: Path) -> None:
    data = make_data()

    html = render_report(data)

    embedded = embedded_report_json(html)
    assert named_run(embedded)["run"] == json.loads(data.run.run.model_dump_json())
    assert embedded["baseline"] is None
    assert embedded["generated_at"] == "2026-01-02T03:04:05Z"
    assert html.startswith("<!doctype html>"), "the template around the data survives"


def test_the_page_carries_the_baseline_run_beside_the_run(
    report_template: Path,
) -> None:
    baseline = make_recorded_run(make_round(assertions={"passed": False}), commit="c1")
    data = make_data(baseline=baseline, via="pr/7")

    embedded = embedded_report_json(render_report(data))

    reported = named_run(embedded)
    assert reported["run"] == json.loads(data.run.run.model_dump_json())
    assert reported["via"] == "pr/7"
    assert embedded["baseline"] == json.loads(baseline.model_dump_json())


def test_an_output_cannot_close_the_data_element(report_template: Path) -> None:
    """A model output is whatever the task returned. The parser ends a script
    element at the first `</script`, whatever the element's type, so one in
    an output would turn the rest of the run into markup and script."""
    hostile = '</script><script>alert("x")</script>&amp;\u2028\u2029'
    run = make_recorded_run(
        make_round(attempts=[make_attempt(output=hostile)]), commit="c0"
    )

    html = render_report(make_data(run))

    _, _, tail = html.partition('id="evaltrack-data">')
    body, _, rest = tail.partition("</script>")
    assert "<" not in body and ">" not in body and "&" not in body
    assert "\u2028" not in body and "\u2029" not in body
    assert "alert" in body, "the output is still in the data"
    assert rest.startswith("<script>window.rendered"), (
        "the template's own script follows"
    )
    # The escapes decode back to the text, so the page renders what was recorded.
    embedded = embedded_report_json(html)
    assert recorded_output(embedded) == hostile


def test_the_page_names_no_remote_asset(report_template: Path) -> None:
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


def test_collected_data_finds_the_mainline() -> None:
    """The mainline entry is the promote that carried the run."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("baseline", run.id, commit="main-0", pr=3, title="Land it")

    data = collect_report_data(
        repo, NamedRun(run=run, via="baseline"), mainline=Mainline(repo)
    )

    assert data.mainline is not None
    assert (data.mainline.commit, data.mainline.pr) == ("main-0", 3)
    assert data.run.via == "baseline"
    assert data.baseline is None, "the baseline run is not compared against itself"


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

    data = collect_report_data(repo, NamedRun(run=viewed), mainline=Mainline(repo))

    reliability = data.history.reliability["test_x"]["test_case"]
    assert reliability.pooled_runs == 3
    assert [p.off_mainline for p in reliability.points][-1] is True
    assert data.mainline is None


def test_collected_data_carries_the_baseline_to_compare_against() -> None:
    """The page offers the comparison the dashboard offers, so the run the
    mainline's `baseline` points at comes along, beside the history."""
    repo = RunRepository(MemoryStore())
    base = make_recorded_run(make_round(), commit="c0", reliability_target=0.9)
    repo.save_run(base)
    repo.move_ref("baseline", base.id, commit="main-0")
    viewed = make_recorded_run(make_round(), commit="c1", reliability_target=0.9)
    repo.save_run(viewed)
    repo.move_ref("pr/9", viewed.id, pr=9)

    data = collect_report_data(repo, NamedRun(run=viewed), mainline=Mainline(repo))

    assert data.baseline is not None and data.baseline.id == base.id
    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 1
    assert [r.name for r in data.refs] == ["pr/9"]


def test_a_baseline_this_version_cannot_read_is_left_out() -> None:
    """During a rolling upgrade a newer evaltrack promotes `baseline`. The
    run itself is readable, and it is what the report is for."""
    store = MemoryStore()
    repo = RunRepository(store)
    base = make_recorded_run(make_round(), commit="c0")
    repo.save_run(base)
    repo.move_ref("baseline", base.id, commit="main-0")
    stored = json.loads(store.read(f"runs/{base.id}.json"))
    stored["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    store.write(json.dumps(stored).encode(), f"runs/{base.id}.json")
    viewed = make_recorded_run(make_round(), commit="c1")
    repo.save_run(viewed)

    data = collect_report_data(repo, NamedRun(run=viewed), mainline=Mainline(repo))

    assert data.baseline is None
    assert data.mainline_error is None, "the mainline itself was read"


def test_collected_history_is_measured_over_the_given_mainline() -> None:
    """A developer's own run is held locally while the team's baseline lives
    on the remote. The report then measures over the remote, as the dashboard
    shows it, and finds no promotion for a run that was never on it."""
    local, remote = RunRepository(MemoryStore()), RunRepository(MemoryStore())
    promoted = make_recorded_run(make_round(), commit="c0", reliability_target=0.9)
    remote.save_run(promoted)
    remote.move_ref("baseline", promoted.id, commit="main-0")
    viewed = make_recorded_run(make_round(), commit="wip", reliability_target=0.9)
    local.save_run(viewed)

    data = collect_report_data(local, NamedRun(run=viewed), mainline=Mainline(remote))

    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 1
    assert data.mainline is None


def test_collected_data_names_the_refs_pointing_at_the_run() -> None:
    """The page shows which refs reach the run, with the PR a ref records,
    the way the dashboard does. A ref pointing elsewhere is not among them."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    other = make_recorded_run(make_round(), commit="c1")
    repo.save_run(run)
    repo.save_run(other)
    repo.move_ref("pr/7", run.id, pr=7, title="Tighten the prompt")
    repo.move_ref("pr/8", other.id, pr=8)
    repo.move_ref("baseline", run.id)

    data = collect_report_data(repo, NamedRun(run=run), mainline=Mainline(None))

    assert [r.name for r in data.refs] == ["baseline", "pr/7"]
    pr = data.refs[1].tip
    assert pr is not None and (pr.pr, pr.title) == (7, "Tighten the prompt")


def test_an_unreachable_mainline_leaves_the_report_without_history() -> None:
    """A local run with the remote down is still a run worth sharing. The
    page says why it carries no history rather than showing none."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    down = RunRepository(RaisingStore(RepositoryUnavailableError("no route to host")))

    data = collect_report_data(repo, NamedRun(run=run), mainline=Mainline(down))

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error is not None and "no route" in data.mainline_error
    assert data.run.run.id == run.id


def test_a_mainline_the_caller_could_not_open_is_explained_in_the_page() -> None:
    """The generator, not the collector, opens the mainline. When it cannot,
    the page still says why it carries no history."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

    data = collect_report_data(
        repo,
        NamedRun(run=run),
        mainline=Mainline(None, "the azure extra is not installed"),
    )

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error == "the azure extra is not installed"


def test_the_page_leaves_out_the_raw_results_an_old_run_carries(
    report_template: Path,
) -> None:
    """A run saved before 0.3.0 holds the runner's own reports beside the
    cases. The page never renders them, and they can be most of the run."""
    run = make_recorded_run(make_round(), commit="c0")
    stored = json.loads(dump_run_json(run))
    stored["tests"]["test_x"]["raw_results"] = [{"cases": [{"output": "x" * 100}]}]
    old_run = parse_run_json(json.dumps(stored).encode())

    embedded = embedded_report_json(render_report(make_data(old_run, baseline=old_run)))

    baseline = embedded["baseline"]
    assert isinstance(baseline, dict)
    for run_json in (named_run(embedded)["run"], baseline):
        test = run_json["tests"]["test_x"]
        assert "raw_results" not in test
        assert test["cases"], "the structured cases still stand"


def test_a_large_value_is_left_out_with_its_preview(report_template: Path) -> None:
    """A report of a large run has to stay a file worth sending, so the page
    gets what the dashboard gets on a first load: the preview and the size."""
    big = "y" * 500
    run = make_recorded_run(
        make_round(attempts=[make_attempt(output=big)]), commit="c0"
    )

    embedded = embedded_report_json(render_report(make_data(run), inline_limit=100))

    output = recorded_output(embedded)
    assert isinstance(output, dict) and set(output) == {"$deferred"}
    envelope = output["$deferred"]
    assert envelope["preview"] == big[:200]
    assert envelope["size"] > 100
    assert (envelope["test"], envelope["field"]) == ("test_x", "output")


def test_every_value_is_embedded_on_request(report_template: Path) -> None:
    big = "y" * 500
    run = make_recorded_run(
        make_round(attempts=[make_attempt(output=big)]), commit="c0"
    )

    embedded = embedded_report_json(render_report(make_data(run), inline_limit=None))

    assert recorded_output(embedded) == big
