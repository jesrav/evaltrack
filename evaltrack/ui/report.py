"""A single-file HTML report of a recorded run. The run is embedded in the page,
so it opens anywhere with no server and no network."""

import importlib.metadata
from datetime import UTC, datetime
from pathlib import Path

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.core.run_record import RunRecord, strip_raw_results
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import MainlineEntry, ReportData, RunHistory
from evaltrack.ui.views import (
    find_mainline_entry,
    load_run_history,
    refs_pointing_at,
)

# Beside the dashboard bundle, so one frontend build ships both.
TEMPLATE_PATH = Path(__file__).parent / "static" / "report.html"

# The element the page reads. The build leaves it empty, and the report fills it.
_DATA_SLOT = '<script type="application/json" id="evaltrack-data"></script>'


def collect_report_data(
    repository: RunRepository,
    run: RunRecord,
    *,
    mainline: RunRepository | None,
    via: str | None = None,
    against: RunRecord | None = None,
    against_via: str | None = None,
    pr_url_template: PrUrlTemplate | None = None,
) -> ReportData:
    """The report's data for `run`, held by `repository`, with its history and
    promotion measured over `mainline`'s `baseline`. The two differ when the
    run is a developer's own and the team's mainline lives elsewhere.

    The runner's own result objects are dropped, as the page never renders
    them. The cross-run history is read only without `against`, since a
    comparison does not show it and it costs a run body per mainline entry.
    A mainline that cannot be reached leaves the report without history and
    says so, as the dashboard drops the column, since the run itself is
    what the report is for.

    Raises:
        CorruptRecordError: when the `baseline` reflog does not parse.
    """
    history = RunHistory()
    mainline_entry: MainlineEntry | None = None
    history_error: str | None = None
    try:
        if against is None:
            history = load_run_history(repository, mainline=mainline, run_id=run.id)
        if mainline is not None:
            mainline_entry = find_mainline_entry(mainline, run.id)
    except RepositoryUnavailableError as exc:
        history, mainline_entry, history_error = RunHistory(), None, str(exc)
    return ReportData(
        run=strip_raw_results(run),
        via=via,
        against=strip_raw_results(against) if against is not None else None,
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


def render_report(data: ReportData) -> str:
    """The report page for `data`, as one self-contained HTML document.

    Raises:
        FileNotFoundError: when the page template is not built.
        ValueError: when the template carries no data slot, so it is not the
            template this version writes into.
    """
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"the report template is not built ({TEMPLATE_PATH} is missing). "
            "In a checkout, run `just frontend_build`."
        ) from None
    if _DATA_SLOT not in template:
        raise ValueError(
            f"{TEMPLATE_PATH} carries no data slot, so it is not the report "
            "template this evaltrack writes. Rebuild it with `just frontend_build`."
        )
    payload = escape_json_for_html(data.model_dump_json())
    return template.replace(
        _DATA_SLOT,
        f'<script type="application/json" id="evaltrack-data">{payload}</script>',
        1,
    )
