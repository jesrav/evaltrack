"""The single-file report. What it embeds, and how it keeps the content of a run inside
the data element."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

import evaltrack.report.page as report_module
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    RunRecord,
    dump_run_json,
    parse_run_json,
)
from evaltrack.report.models import Mainline, ReportData
from evaltrack.report.page import collect_report_data, render_report
from evaltrack.repositories import RunRepository

from ..factories import make_attempt, make_round
from ..fakes import MemoryStore, RaisingStore
from ..report_support import embedded_report_json, embedded_run, recorded_output
from .conftest import make_recorded_run

GENERATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def make_data(
    run: RunRecord | None = None,
    *,
    via_ref: str | None = None,
    baseline: RunRecord | None = None,
) -> ReportData:
    return ReportData(
        run=run or make_recorded_run(make_round(), commit="c0"),
        via_ref=via_ref,
        baseline=baseline,
        generated_at=GENERATED_AT,
        generated_by="0.0.0",
    )


def test_the_page_carries_the_run_as_the_api_serves_it(report_template: Path) -> None:
    data = make_data()

    html = render_report(data)

    embedded = embedded_report_json(html)
    assert embedded_run(embedded) == json.loads(data.run.model_dump_json())
    assert embedded["baseline"] is None
    assert embedded["generated_at"] == "2026-01-02T03:04:05Z"
    assert html.startswith("<!doctype html>"), "the template around the data survives"


def test_the_page_carries_the_baseline_run_beside_the_run(
    report_template: Path,
) -> None:
    baseline = make_recorded_run(make_round(assertions={"passed": False}), commit="c1")
    data = make_data(baseline=baseline, via_ref="pr/7")

    embedded = embedded_report_json(render_report(data))

    assert embedded_run(embedded) == json.loads(data.run.model_dump_json())
    assert embedded["via_ref"] == "pr/7"
    assert embedded["baseline"] == json.loads(baseline.model_dump_json())


def test_an_output_cannot_close_the_data_element(report_template: Path) -> None:
    """An output can hold any text. The HTML parser ends a script element at the first
    `</script`, whatever the type of the element. Without the escapes, the rest of the
    run becomes markup and script."""
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
    """A template without the data slot has no place for the run. The report must fail,
    and not write a page with no run in it."""
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

    data = collect_report_data(repo, run, mainline=Mainline(repo), via_ref="baseline")

    assert data.mainline is not None
    assert (data.mainline.commit, data.mainline.pr) == ("main-0", 3)
    assert data.via_ref == "baseline"
    assert data.baseline is None, "the baseline run is not compared against itself"


def test_collected_history_is_measured_over_the_repository_baseline() -> None:
    """The report holds the reliability of the run, measured over the promoted history
    of the mainline."""
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

    data = collect_report_data(repo, viewed, mainline=Mainline(repo))

    reliability = data.history.reliability["test_x"]["test_case"]
    assert reliability.pooled_runs == 3
    assert [p.off_mainline for p in reliability.points][-1] is True
    assert data.mainline is None


def test_collected_data_carries_the_baseline_to_compare_against() -> None:
    """The report holds the run that the mainline's `baseline` points at, and the
    history. The page can then compare against that run."""
    repo = RunRepository(MemoryStore())
    base = make_recorded_run(make_round(), commit="c0", reliability_target=0.9)
    repo.save_run(base)
    repo.move_ref("baseline", base.id, commit="main-0")
    viewed = make_recorded_run(make_round(), commit="c1", reliability_target=0.9)
    repo.save_run(viewed)
    repo.move_ref("pr/9", viewed.id, pr=9)

    data = collect_report_data(repo, viewed, mainline=Mainline(repo))

    assert data.baseline is not None and data.baseline.id == base.id
    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 1
    assert [r.name for r in data.refs] == ["pr/9"]


def test_a_baseline_this_version_cannot_read_is_left_out() -> None:
    """A newer evaltrack can promote a `baseline` that this one cannot read. The report
    then has no baseline run, and the mainline is still read."""
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

    data = collect_report_data(repo, viewed, mainline=Mainline(repo))

    assert data.baseline is None
    assert data.mainline_error is None, "the mainline itself was read"


def test_collected_history_is_measured_over_the_given_mainline() -> None:
    """A local run is measured over the remote's baseline. A run that was never promoted
    has no mainline entry."""
    local, remote = RunRepository(MemoryStore()), RunRepository(MemoryStore())
    promoted = make_recorded_run(make_round(), commit="c0", reliability_target=0.9)
    remote.save_run(promoted)
    remote.move_ref("baseline", promoted.id, commit="main-0")
    viewed = make_recorded_run(make_round(), commit="wip", reliability_target=0.9)
    local.save_run(viewed)

    data = collect_report_data(local, viewed, mainline=Mainline(remote))

    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 1
    assert data.mainline is None


def test_collected_data_names_the_refs_pointing_at_the_run() -> None:
    """The report names the refs that point at the run, each with the PR that it
    records. A ref that points at another run is not included."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    other = make_recorded_run(make_round(), commit="c1")
    repo.save_run(run)
    repo.save_run(other)
    repo.move_ref("pr/7", run.id, pr=7, title="Tighten the prompt")
    repo.move_ref("pr/8", other.id, pr=8)
    repo.move_ref("baseline", run.id)

    data = collect_report_data(repo, run, mainline=Mainline(None))

    assert [r.name for r in data.refs] == ["baseline", "pr/7"]
    pr = data.refs[1].tip
    assert pr is not None and (pr.pr, pr.title) == (7, "Tighten the prompt")


def test_an_unreachable_mainline_leaves_the_report_without_history() -> None:
    """When the remote is down, the report still holds the run. It says why there is no
    history."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    down = RunRepository(RaisingStore(RepositoryUnavailableError("no route to host")))

    data = collect_report_data(repo, run, mainline=Mainline(down))

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error is not None and "no route" in data.mainline_error
    assert data.run.id == run.id


def test_a_mainline_the_caller_could_not_open_is_explained_in_the_page() -> None:
    """When the caller cannot open the mainline, it passes the reason, and the report
    holds that reason."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

    data = collect_report_data(
        repo,
        run,
        mainline=Mainline(None, "the azure extra is not installed"),
    )

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error == "the azure extra is not installed"


def test_the_page_leaves_out_the_raw_results_an_old_run_carries(
    report_template: Path,
) -> None:
    """A run saved before 0.3.0 also holds the raw results of the eval runner. The page
    does not show them, so the report leaves them out."""
    run = make_recorded_run(make_round(), commit="c0")
    stored = json.loads(dump_run_json(run))
    stored["tests"]["test_x"]["raw_results"] = [{"cases": [{"output": "x" * 100}]}]
    old_run = parse_run_json(json.dumps(stored).encode())

    embedded = embedded_report_json(render_report(make_data(old_run, baseline=old_run)))

    baseline = embedded["baseline"]
    assert isinstance(baseline, dict)
    for run_json in (embedded_run(embedded), baseline):
        test = run_json["tests"]["test_x"]
        assert "raw_results" not in test
        assert test["cases"], "the structured cases still stand"


def test_a_large_value_is_left_out_with_its_preview(report_template: Path) -> None:
    """A large value is left out of the report, so that the file stays small. Its
    preview and its size take its place."""
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


def test_a_baseline_reflog_that_does_not_parse_leaves_the_run_alone() -> None:
    """When the `baseline` reflog does not parse, the report holds the run alone and
    says why."""
    store = MemoryStore()
    mainline = RunRepository(store)
    promoted = make_recorded_run(make_round(), commit="c0")
    mainline.save_run(promoted)
    mainline.move_ref("baseline", promoted.id, commit="main-0")
    store.append(b'{"run_id": "01KXB8', "refs/baseline.log.jsonl")
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c1")
    repo.save_run(run)

    data = collect_report_data(repo, run, mainline=Mainline(mainline))

    assert data.run.id == run.id
    assert data.history.reliability == {} and data.baseline is None
    assert data.mainline_error is not None
