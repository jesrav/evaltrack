"""A single-file HTML report of a recorded run. The run is embedded in the page,
so it opens anywhere with no server and no network."""

import importlib.metadata
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.refs import BASELINE_REF
from evaltrack.core.run_record import RunRecord, dump_plain, dump_plain_json
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import MainlineEntry, ReportData, RunHistory
from evaltrack.ui.views import mainline_entry_in, refs_pointing_at, run_history_over

# Beside the dashboard bundle, so one frontend build ships both.
TEMPLATE_PATH = Path(__file__).parent / "static" / "report.html"

# The element the page reads. The build leaves it empty, and the report fills it.
_DATA_SLOT = '<script type="application/json" id="evaltrack-data"></script>'


def collect_report_data(
    repository: RunRepository,
    run: RunRecord,
    *,
    mainline: RunRepository | None,
    mainline_error: str | None = None,
    via: str | None = None,
    against: RunRecord | None = None,
    against_via: str | None = None,
    pr_url_template: PrUrlTemplate | None = None,
) -> ReportData:
    """The report's data for `run`, held by `repository`, with its history and
    promotion measured over `mainline`'s `baseline`. The two differ when the
    run is a developer's own and the team's mainline lives elsewhere.
    `mainline_error` says why there is no mainline when the caller could not
    open the one it wanted, and the page carries it as `history_error`.

    The cross-run history is read only without `against`, since a
    comparison does not show it and it costs a run body per mainline entry.
    A mainline that cannot be reached leaves the report without history and
    says so, as the dashboard drops the column, since the run itself is
    what the report is for.

    Raises:
        CorruptRecordError: when the `baseline` reflog does not parse.
    """
    history = RunHistory()
    mainline_entry: MainlineEntry | None = None
    history_error = mainline_error
    if mainline is not None:
        try:
            # One read serves both, since the history is a window of the same
            # reflog the entry is found in.
            reflog = list(mainline.get_reflog(BASELINE_REF))
            if against is None:
                history = run_history_over(mainline, reflog, viewed=run)
            mainline_entry = mainline_entry_in(reflog, run.id)
        except RepositoryUnavailableError as exc:
            history, mainline_entry, history_error = RunHistory(), None, str(exc)
    return ReportData(
        run=run,
        via=via,
        against=against,
        against_via=against_via,
        refs=refs_pointing_at(repository, run.id),
        history=history,
        mainline=mainline_entry,
        history_error=history_error,
        pr_url_template=pr_url_template,
        generated_at=datetime.now(UTC),
        generated_by=importlib.metadata.version("evaltrack"),
    )


def escape_json_for_html(json_text: str) -> str:
    """Rewrite `json_text` so it can sit inside a `<script>` element whatever it
    holds. The parser ends the element at the first `</script` and knows no
    escaping inside it, so every `<`, `>` and `&` becomes its JSON escape,
    which decodes back to the same character. The two Unicode line
    terminators go the same way, since a page is read as JavaScript source by
    some tools and they end a line there.
    """
    return (
        json_text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


@cache
def _load_template(path: Path) -> tuple[str, str]:
    """The template split at its data slot. Cached, because the built page is
    fixed for the life of the process and the dashboard renders it per download.
    A failure is not cached, so a build that lands later is picked up."""
    try:
        template = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"the report template is not built ({path} is missing). "
            "In a checkout, run `just frontend_build`."
        ) from None
    head, slot, tail = template.partition(_DATA_SLOT)
    if not slot:
        raise ValueError(
            f"{path} carries no data slot, so it is not the report "
            "template this evaltrack writes. Rebuild it with `just frontend_build`."
        )
    return head, tail


def _dump_report_json(data: ReportData) -> str:
    """The report as compact JSON, written the way a stored run is."""
    plain = dump_plain(data)
    for run in (plain["run"], plain["against"]):
        if run is None:
            continue
        for test in run["tests"].values():
            # A run saved before 0.3.0 holds the runner's own reports too,
            # which the page never renders.
            test.pop("raw_results", None)
    return dump_plain_json(plain).decode()


def render_report(data: ReportData) -> str:
    """The report page for `data`, as one self-contained HTML document.

    Raises:
        FileNotFoundError: when the page template is not built.
        ValueError: when the template carries no data slot, so it is not the
            template this version writes into.
    """
    head, tail = _load_template(TEMPLATE_PATH)
    payload = escape_json_for_html(_dump_report_json(data))
    return (
        f'{head}<script type="application/json" id="evaltrack-data">{payload}'
        f"</script>{tail}"
    )
