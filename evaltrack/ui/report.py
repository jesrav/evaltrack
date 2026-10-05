"""A single-file HTML report of a recorded run. The run is embedded in the page,
so it opens anywhere with no server and no network."""

import importlib.metadata
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import CorruptRecordError, RepositoryUnavailableError
from evaltrack.core.run_record import RunRecord, dump_plain, dump_plain_json
from evaltrack.repositories import RunRepository
from evaltrack.ui.models import (
    Mainline,
    MainlineEntry,
    NamedRun,
    ReportData,
    RunHistory,
)
from evaltrack.ui.run_view import INLINE_VALUE_BYTES, build_run_view
from evaltrack.ui.views import (
    find_mainline_entry,
    load_baseline_run,
    load_run_history,
    refs_pointing_at,
)

# Beside the dashboard bundle, so one frontend build ships both.
TEMPLATE_PATH = Path(__file__).parent / "static" / "report.html"

# The element the page reads. The build leaves it empty, and the report fills it.
_SLOT_OPEN = '<script type="application/json" id="evaltrack-data">'
_SLOT_CLOSE = "</script>"


def collect_report_data(
    repository: RunRepository,
    run: NamedRun,
    *,
    mainline: Mainline,
    pr_url_template: PrUrlTemplate | None = None,
) -> ReportData:
    """The report's data for `run`, held by `repository`: the run, and what
    `mainline` says about it. That is its history and promotion over the
    `baseline` reflog, and the `baseline` run to compare against. The two
    repositories differ when the run is a developer's own and the team's
    mainline lives elsewhere.

    A mainline that is missing, cannot be reached, or holds a `baseline`
    reflog that does not parse leaves the report with the run alone and says
    why, as the dashboard drops the column, since the run itself is what the
    report is for.
    """
    history = RunHistory()
    mainline_entry: MainlineEntry | None = None
    baseline: RunRecord | None = None
    mainline_error = mainline.error
    if mainline.repository is not None:
        try:
            history = load_run_history(mainline.repository, viewed=run.run)
            mainline_entry = find_mainline_entry(mainline.repository, run.run.id)
            baseline = load_baseline_run(mainline.repository, other_than=run.run.id)
        except (RepositoryUnavailableError, CorruptRecordError) as exc:
            history, mainline_entry, baseline = RunHistory(), None, None
            mainline_error = str(exc)
    return ReportData(
        run=run,
        baseline=baseline,
        refs=refs_pointing_at(repository, run.run.id),
        history=history,
        mainline=mainline_entry,
        mainline_error=mainline_error,
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
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
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
    head, slot, tail = template.partition(_SLOT_OPEN + _SLOT_CLOSE)
    if not slot:
        raise ValueError(
            f"{path} carries no data slot, so it is not the report "
            "template this evaltrack writes. Rebuild it with `just frontend_build`."
        )
    return head, tail


def _dump_report_json(data: ReportData, *, inline_limit: int | None) -> str:
    """The report as compact JSON. Each run is embedded as the dashboard opens
    it, with every value over `inline_limit` bytes replaced by its preview and
    size, so a report of a large run stays a file worth sending."""
    # The runs are dumped once, as their views, and not also whole.
    runs: dict[str, Any] = {
        "run": {
            "run": build_run_view(data.run.run, limit=inline_limit),
            "via": data.run.via,
        },
        "baseline": (
            build_run_view(data.baseline, limit=inline_limit)
            if data.baseline is not None
            else None
        ),
    }
    rest = dump_plain(data, include=set(ReportData.model_fields) - set(runs))
    return dump_plain_json(runs | rest).decode()


def render_report(
    data: ReportData, *, inline_limit: int | None = INLINE_VALUE_BYTES
) -> str:
    """The report page for `data`, as one self-contained HTML document. A value
    over `inline_limit` bytes is left out, as the dashboard leaves it out of a
    first load, and None embeds every value whole.

    Raises:
        FileNotFoundError: when the page template is not built.
        ValueError: when the template carries no data slot, so it is not the
            template this version writes into.
    """
    head, tail = _load_template(TEMPLATE_PATH)
    payload = escape_json_for_html(_dump_report_json(data, inline_limit=inline_limit))
    return f"{head}{_SLOT_OPEN}{payload}{_SLOT_CLOSE}{tail}"
