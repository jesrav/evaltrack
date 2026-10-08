"""The single-file report. What it embeds, and how it keeps the content of a run inside
the data element."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    RunRecord,
)
from evaltrack.report.page import (
    ReportData,
    collect_report_data,
    render_report,
)
from evaltrack.repositories import RunRepository
from evaltrack.views.mainline import NoRemote

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


def test_a_missing_template_names_the_build(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="frontend_build"):
        render_report(make_data(), template_path=tmp_path / "missing.html")


def test_a_template_without_a_slot_is_refused(tmp_path: Path) -> None:
    """A template without the data slot has no place for the run. The report must fail,
    and not write a page with no run in it."""
    path = tmp_path / "report.html"
    path.write_text("<!doctype html><title>other</title>", encoding="utf-8")

    with pytest.raises(ValueError, match="data slot"):
        render_report(make_data(), template_path=path)


def test_collected_data_finds_the_mainline() -> None:
    """The mainline entry is the promote that carried the run."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref("baseline", run.id, commit="main-0", pr=3, title="Land it")

    data = collect_report_data(repo, run, remote=repo, via_ref="baseline")

    assert data.mainline is not None
    assert (data.mainline.commit, data.mainline.pr) == ("main-0", 3)
    assert data.via_ref == "baseline"
    assert data.baseline is None, "the baseline run is not compared against itself"


def test_collected_history_is_measured_over_the_remote_baseline() -> None:
    """The report holds the reliability of the run, measured over the promoted history
    on the remote. A run never promoted is drawn over it, with no mainline entry."""
    local, remote = RunRepository(MemoryStore()), RunRepository(MemoryStore())
    for i, ok in enumerate([True, False, True]):
        promoted = make_recorded_run(
            make_round(assertions={"passed": ok}),
            commit=f"c{i}",
            reliability_target=0.9,
        )
        remote.save_run(promoted)
        remote.move_ref("baseline", promoted.id, commit=f"main-{i}")
    viewed = make_recorded_run(make_round(), commit="pr", reliability_target=0.9)
    local.save_run(viewed)

    data = collect_report_data(local, viewed, remote=remote)

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
    repo.move_ref("pr/9", viewed.id, pr=9, title="Tighten the prompt")

    data = collect_report_data(repo, viewed, remote=repo)

    assert data.baseline is not None and data.baseline.id == base.id
    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 1
    assert [r.name for r in data.refs] == ["pr/9"], "only the refs at this run"
    tip = data.refs[0].tip
    assert tip is not None and (tip.pr, tip.title) == (9, "Tighten the prompt")


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

    data = collect_report_data(repo, viewed, remote=repo)

    assert data.baseline is None
    assert data.mainline_error is None, "the mainline itself was read"


def test_an_unreachable_mainline_leaves_the_report_without_history(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the remote is down, the report still holds the run. The page says why in
    fixed words, and the storage error, which can name a host, goes to the log."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    down = RunRepository(RaisingStore(RepositoryUnavailableError("no route to host10")))

    data = collect_report_data(repo, run, remote=down)

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error == "the remote was not reached"
    assert data.run.id == run.id
    assert "host10" in caplog.text, "the detail is logged"


def test_a_mainline_the_caller_could_not_open_is_explained_in_the_page() -> None:
    """When the caller cannot open the mainline, it passes the reason, and the report
    holds that reason."""
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)

    data = collect_report_data(
        repo,
        run,
        remote=NoRemote.DID_NOT_OPEN,
    )

    assert data.history.reliability == {}
    assert data.mainline is None
    assert data.mainline_error == "the remote did not open"


def test_a_large_value_is_left_out_unless_every_value_is_asked_for(
    report_template: Path,
) -> None:
    """A large value is left out of the report, so that the file stays small. Its
    preview and its size take its place. No limit embeds it whole."""
    big = "y" * 500
    run = make_recorded_run(
        make_round(attempts=[make_attempt(output=big)]), commit="c0"
    )

    small = embedded_report_json(render_report(make_data(run), inline_limit=100))
    full = embedded_report_json(render_report(make_data(run), inline_limit=None))

    output = recorded_output(small)
    assert isinstance(output, dict) and set(output) == {"$deferred"}
    envelope = output["$deferred"]
    assert envelope["preview"] == big[:200]
    assert envelope["size"] > 100
    assert (envelope["test"], envelope["field"]) == ("test_x", "output")
    assert recorded_output(full) == big


def test_a_baseline_reflog_that_does_not_parse_leaves_the_run_alone() -> None:
    """When the `baseline` reflog does not parse, the report holds the run alone and
    says why."""
    store = MemoryStore()
    remote = RunRepository(store)
    promoted = make_recorded_run(make_round(), commit="c0")
    remote.save_run(promoted)
    remote.move_ref("baseline", promoted.id, commit="main-0")
    store.append(b'{"run_id": "01KXB8', "refs/baseline.log.jsonl")
    repo = RunRepository(MemoryStore())
    run = make_recorded_run(make_round(), commit="c1")
    repo.save_run(run)

    data = collect_report_data(repo, run, remote=remote)

    assert data.run.id == run.id
    assert data.history.reliability == {} and data.baseline is None
    assert data.mainline_error is not None


class _CountingStore(MemoryStore):
    """A store that counts how often each path is read."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: dict[str, int] = {}

    def read(self, path: str) -> bytes:
        self.reads[path] = self.reads.get(path, 0) + 1
        return super().read(path)


def test_the_baseline_reflog_is_read_once_per_report() -> None:
    """The history, the mainline entry and the baseline run all come from the
    reflog. On a blob store each read is a round trip."""
    store = _CountingStore()
    remote = RunRepository(store)
    base = make_recorded_run(make_round(), commit="c0")
    remote.save_run(base)
    remote.move_ref("baseline", base.id, commit="main-0")
    local = RunRepository(MemoryStore())
    viewed = make_recorded_run(make_round(), commit="c1")
    local.save_run(viewed)
    store.reads.clear()

    data = collect_report_data(local, viewed, remote=remote)

    assert data.baseline is not None and data.mainline_error is None
    assert store.reads["refs/baseline.log.jsonl"] == 1


def test_a_promoted_run_that_does_not_parse_is_left_out_of_the_history() -> None:
    """One damaged run body on the mainline costs that run, not the history,
    and it is not blamed on the reflog."""
    store = MemoryStore()
    repo = RunRepository(store)
    runs = [make_recorded_run(make_round(), commit=f"c{i}") for i in range(3)]
    for i, promoted in enumerate(runs):
        repo.save_run(promoted)
        repo.move_ref("baseline", promoted.id, commit=f"main-{i}")
    store.write(b"{not json", f"runs/{runs[0].id}.json")
    viewed = make_recorded_run(make_round(), commit="pr")
    repo.save_run(viewed)

    data = collect_report_data(repo, viewed, remote=repo)

    assert data.mainline_error is None
    assert data.history.reliability["test_x"]["test_case"].pooled_runs == 2
    assert data.baseline is not None and data.baseline.id == runs[2].id
