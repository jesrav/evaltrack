"""`evaltrack report`, which writes a run as a single HTML file."""

import json
from pathlib import Path

import pytest

import evaltrack.ui.report as report_module
from evaltrack.cli import main
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_record import RUN_SCHEMA_VERSION
from evaltrack.repositories import open_repository

from ..factories import make_attempt, make_round
from ..fakes import RaisingStore, mount_fake_azure
from ..report_support import embedded_report_json, embedded_run, recorded_output
from .helpers import (
    configure_local,
    configure_repositories,
    run_cli,
    seed_run,
    seed_run_in_repo,
)

pytestmark = pytest.mark.usefixtures("report_template")


def test_report_writes_one_file_holding_the_run(tmp_path: Path) -> None:
    url = str(tmp_path / "repo")
    run_id = seed_run_in_repo(url)
    output = tmp_path / "out" / "run.html"
    output.parent.mkdir()

    result = run_cli(
        ["report", "--run-id", run_id, "--repository", url, "--output", str(output)]
    )

    assert result.code == 0
    assert [p.name for p in output.parent.iterdir()] == ["run.html"], (
        "the report is one file with no assets beside it"
    )
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert embedded_run(data)["id"] == run_id
    assert data["baseline"] is None
    assert run_id in result.out and str(output) in result.out


def test_report_defaults_to_a_file_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = str(tmp_path / "repo")
    run_id = seed_run_in_repo(url)
    monkeypatch.chdir(tmp_path)

    result = run_cli(["report", "--run-id", run_id, "--repository", url])

    assert result.code == 0
    assert (tmp_path / "evaltrack-report.html").is_file()


def test_report_to_stdout_prints_only_the_page(tmp_path: Path) -> None:
    url = str(tmp_path / "repo")
    run_id = seed_run_in_repo(url)

    result = run_cli(
        ["report", "--run-id", run_id, "--repository", url, "--output", "-"]
    )

    assert result.code == 0
    assert result.out.startswith("<!doctype html>")
    assert result.out.rstrip().endswith("</html>"), "nothing follows the page"
    assert embedded_run(embedded_report_json(result.out)) is not None


def test_report_by_ref_takes_the_tip_and_names_the_ref(tmp_path: Path) -> None:
    url = str(tmp_path / "repo")
    older = seed_run_in_repo(url)
    newer = seed_run_in_repo(url)
    repo = open_repository(url)
    repo.move_ref("pr/12", older, pr=12)
    repo.move_ref("pr/12", newer, pr=12)
    output = tmp_path / "report.html"

    result = run_cli(
        ["report", "--ref", "pr/12", "--repository", url, "--output", str(output)]
    )

    assert result.code == 0
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert embedded_run(data)["id"] == newer
    assert data["via_ref"] == "pr/12"
    refs = data["refs"]
    assert isinstance(refs, list) and [r["name"] for r in refs] == ["pr/12"], (
        "the page can show which refs reach the run, and the PR they record"
    )


def test_report_carries_the_baseline_to_compare_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CI case: the PR's run with the mainline's baseline run beside it,
    so the page can show what changed, with no flag asking for it."""
    url = str(tmp_path / "repo")
    monkeypatch.setenv("EVALTRACK_REMOTE", url)
    baseline = seed_run_in_repo(url)
    pr_run = seed_run_in_repo(url)
    repo = open_repository(url)
    repo.move_ref("baseline", baseline)
    repo.move_ref("pr/3", pr_run, pr=3)
    output = tmp_path / "report.html"

    result = run_cli(
        ["report", "--ref", "pr/3", "--repository", url, "--output", str(output)]
    )

    assert result.code == 0
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    embedded_baseline = data["baseline"]
    assert isinstance(embedded_baseline, dict)
    assert (embedded_run(data)["id"], embedded_baseline["id"]) == (pr_run, baseline)
    assert data["via_ref"] == "pr/3"
    assert baseline in result.out and pr_run in result.out


def test_report_missing_run_exits_1_and_writes_nothing(tmp_path: Path) -> None:
    """A run the repository does not hold is the one outcome that exits 1,
    as it is for `export`, so a script can tell it from a crash."""
    url = str(tmp_path / "repo")
    seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(
        [
            "report",
            "--run-id",
            "01J9Z3QW2KJ5H8VN4TQY7B6MDC",
            "--repository",
            url,
            "--output",
            str(output),
        ]
    )

    assert result.code == 1, "a run the repository does not hold exits 1"
    assert "not found" in result.err and url in result.err
    assert not output.exists()
    assert "Traceback" not in result.err, "a missing run is an outcome, not a crash"


def test_report_missing_ref_exits_1(tmp_path: Path) -> None:
    url = str(tmp_path / "repo")
    seed_run_in_repo(url)

    result = run_cli(["report", "--ref", "pr/404", "--repository", url])

    assert result.code == 1, "a ref the repository does not hold exits 1"
    assert "pr/404" in result.err and "not found" in result.err


def test_report_before_there_is_a_baseline_is_of_the_run_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first PR of a project has no baseline yet. Nothing was asked for
    that cannot be had, so the report is written with nothing to warn about."""
    url = str(tmp_path / "repo")
    monkeypatch.setenv("EVALTRACK_REMOTE", url)
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(
        ["report", "--run-id", run_id, "--repository", url, "--output", str(output)]
    )

    assert result.code == 0
    assert "warning" not in result.err
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert data["baseline"] is None
    assert data["mainline_error"] is None


def test_report_of_the_baseline_run_has_nothing_to_compare_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run compared against itself shows nothing, which the dashboard does
    not offer either."""
    url = str(tmp_path / "repo")
    monkeypatch.setenv("EVALTRACK_REMOTE", url)
    run_id = seed_run_in_repo(url)
    open_repository(url).move_ref("baseline", run_id)
    output = tmp_path / "report.html"

    result = run_cli(
        ["report", "--ref", "baseline", "--repository", url, "--output", str(output)]
    )

    assert result.code == 0
    assert "warning" not in result.err
    assert embedded_report_json(output.read_text(encoding="utf-8"))["baseline"] is None


def test_report_with_a_baseline_in_another_stored_format_is_of_the_run_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """During a rolling upgrade a newer evaltrack promotes `baseline`, and an
    older one reports a pull request. The run itself is readable, and it is
    what the report is for."""
    url = str(tmp_path / "repo")
    monkeypatch.setenv("EVALTRACK_REMOTE", url)
    promoted = seed_run_in_repo(url)
    open_repository(url).move_ref("baseline", promoted)
    stored_path = tmp_path / "repo" / "runs" / f"{promoted}.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    stored["run_schema_version"] = RUN_SCHEMA_VERSION + 1
    stored_path.write_text(json.dumps(stored), encoding="utf-8")
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(
        ["report", "--run-id", run_id, "--repository", url, "--output", str(output)]
    )

    assert result.code == 0, "a baseline this evaltrack cannot read is not a failure"
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert embedded_run(data)["id"] == run_id
    assert data["baseline"] is None


def test_report_defaults_to_the_configured_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The documented use is a CI job, which names the remote once in the
    environment, so the command defaults there like `push` and `promote`."""
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    run_id = seed_run(repos.remote)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--output", str(output)])

    assert result.code == 0
    assert repos.remote in result.err, "the resolved repository is announced"
    assert output.is_file()


def test_report_without_a_built_template_says_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable install has no built frontend until `just frontend_build`
    has run. The failure has to name that, not a missing file."""
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", tmp_path / "missing.html")
    url = str(tmp_path / "repo")
    run_id = seed_run_in_repo(url)

    result = run_cli(["report", "--run-id", run_id, "--repository", url])

    assert result.code == 2, "an unbuilt template is something to fix"
    assert "frontend_build" in result.err
    assert "Traceback" not in result.err


def test_report_needs_exactly_one_way_of_naming_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "--repository", "x"])
    assert excinfo.value.code == 2, "naming no run is a usage error"
    assert "--run-id" in capsys.readouterr().err


def test_report_of_a_local_run_measures_history_over_the_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The team's baseline lives on the remote, so a run held locally is
    still drawn over it, as the dashboard draws it."""
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    promoted = seed_run_in_repo(repos.remote)
    open_repository(repos.remote).move_ref("baseline", promoted, commit="main-0")
    run_id = seed_run_in_repo(repos.local)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--local", "--output", str(output)])

    assert result.code == 0
    assert repos.remote in result.err, "the mainline read is announced"
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    history = data["history"]
    assert isinstance(history, dict)
    assert history["reliability"]["test_x"]["test_case"]["pooled_runs"] == 1


def test_report_without_a_remote_has_no_history_and_no_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mainline is the remote and nothing else. A `baseline` in the named
    repository is not it, so the page neither draws over it nor compares
    against it."""
    url = str(tmp_path / "repo")
    configure_local(tmp_path, url)
    monkeypatch.chdir(tmp_path)
    promoted = seed_run_in_repo(url)
    open_repository(url).move_ref("baseline", promoted, commit="main-0")
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--local", "--output", str(output)])

    assert result.code == 0, "no remote is not a failure of the report"
    assert result.err.count("warning") == 1, "one cause, told once"
    assert "no remote configured" in result.err
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert data["history"] == {"reliability": {}, "score_history": {}}
    assert data["baseline"] is None
    assert data["mainline_error"] is not None


def test_report_carries_the_configured_pr_link_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = str(tmp_path / "repo")
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nremote = "{url}"\n'
        'pr_url_template = "https://example.test/pull/{pr}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--output", str(output)])

    assert result.code == 0
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert data["pr_url_template"] == "https://example.test/pull/{pr}"


def test_report_of_a_local_run_with_the_remote_down_has_no_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Offline, a developer can still share a local run. The warning names
    the remote and the page carries the run without history."""
    url = str(tmp_path / "local")
    configure_local(tmp_path, url)
    remote = mount_fake_azure(
        monkeypatch, RaisingStore(RepositoryUnavailableError("no route to host"))
    )
    monkeypatch.setenv("EVALTRACK_REMOTE", remote)
    monkeypatch.chdir(tmp_path)
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--local", "--output", str(output)])

    assert result.code == 0, "an unreachable mainline does not fail the report"
    assert result.err.count("warning") == 1 and remote in result.err
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert data["history"] == {"reliability": {}, "score_history": {}}
    assert data["mainline_error"] is not None


def test_report_of_a_local_run_compares_against_the_remote_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The team's baseline lives on the remote and never in the local
    repository, so that is the run a local run is compared against."""
    repos = configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    promoted = seed_run_in_repo(repos.remote)
    open_repository(repos.remote).move_ref("baseline", promoted, commit="main-0")
    run_id = seed_run_in_repo(repos.local)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--local", "--output", str(output)])

    assert result.code == 0
    assert "warning" not in result.err
    baseline = embedded_report_json(output.read_text(encoding="utf-8"))["baseline"]
    assert isinstance(baseline, dict) and baseline["id"] == promoted


def test_report_with_a_remote_that_cannot_be_opened_has_no_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A remote whose backend is not installed, or whose URL is malformed,
    is as far out of reach as one that is down, and the local run is still
    the report's subject."""
    url = str(tmp_path / "local")
    configure_local(tmp_path, url)
    remote = "nosuchscheme://team/evals"
    monkeypatch.setenv("EVALTRACK_REMOTE", remote)
    monkeypatch.chdir(tmp_path)
    run_id = seed_run_in_repo(url)
    output = tmp_path / "report.html"

    result = run_cli(["report", "--run-id", run_id, "--local", "--output", str(output)])

    assert result.code == 0, "an unopenable mainline does not fail the report"
    assert result.err.count("warning") == 1 and remote in result.err
    data = embedded_report_json(output.read_text(encoding="utf-8"))
    assert data["history"] == {"reliability": {}, "score_history": {}}
    assert data["mainline_error"] is not None


def seed_run_with_a_large_output(url: str) -> tuple[str, str]:
    """A run whose one output is well over the 16 KB the page keeps inline.
    Returns the run id and the output."""
    big = "z" * 40_000
    rec = EvalRecorder()
    rec.add_round("test_x", make_round(attempts=[make_attempt(output=big)]))
    run = rec.to_run_record()
    open_repository(url).save_run(run)
    return run.id, big


def test_report_leaves_a_large_value_out_unless_asked_for_it_whole(
    tmp_path: Path,
) -> None:
    url = str(tmp_path / "repo")
    run_id, big = seed_run_with_a_large_output(url)
    small, full = tmp_path / "small.html", tmp_path / "full.html"

    assert (
        run_cli(
            ["report", "--run-id", run_id, "--repository", url, "--output", str(small)]
        ).code
        == 0
    )
    assert (
        run_cli(
            [
                "report",
                "--run-id",
                run_id,
                "--repository",
                url,
                "--full",
                "--output",
                str(full),
            ]
        ).code
        == 0
    )

    left_out = recorded_output(embedded_report_json(small.read_text(encoding="utf-8")))
    assert isinstance(left_out, dict) and "$deferred" in left_out
    assert (
        recorded_output(embedded_report_json(full.read_text(encoding="utf-8"))) == big
    )
    assert small.stat().st_size < full.stat().st_size - 30_000
