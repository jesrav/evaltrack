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
from evaltrack.report.models import Mainline, ReportData
from evaltrack.repositories import RunRepository
from evaltrack.views.mainline import (
    find_mainline_entry,
    load_baseline_run,
    load_run_history,
)
from evaltrack.views.models import MainlineEntry, RunHistory
from evaltrack.views.refs import refs_pointing_at
from evaltrack.views.run_view import INLINE_VALUE_BYTES, build_run_view

# The frontend build writes this file.
TEMPLATE_PATH = Path(__file__).parent / "static" / "report.html"

# The element the page reads. The build leaves it empty, and the report fills it.
_SLOT_OPEN = '<script type="application/json" id="evaltrack-data">'
_SLOT_CLOSE = "</script>"


def collect_report_data(
    repository: RunRepository,
    run: RunRecord,
    *,
    mainline: Mainline,
    via_ref: str | None = None,
    pr_url_template: PrUrlTemplate | None = None,
) -> ReportData:
    """The data of a report for `run`, which `repository` holds. It is the run and what
    `mainline` says about it. That is the history and the promotion of the run, and the
    `baseline` run to compare against. `via_ref` is the ref that the report was asked
    for by.

    If the mainline is missing, cannot be reached, or holds a `baseline` reflog that
    does not parse, the report has the run alone and `mainline_error` says why.
    """
    history = RunHistory()
    mainline_entry: MainlineEntry | None = None
    baseline: RunRecord | None = None
    mainline_error = mainline.error
    if mainline.repository is not None:
        try:
            history = load_run_history(mainline.repository, viewed=run)
            mainline_entry = find_mainline_entry(mainline.repository, run.id)
            baseline = load_baseline_run(mainline.repository, other_than=run.id)
        except (RepositoryUnavailableError, CorruptRecordError) as exc:
            history, mainline_entry, baseline = RunHistory(), None, None
            mainline_error = str(exc)
    return ReportData(
        run=run,
        via_ref=via_ref,
        baseline=baseline,
        refs=refs_pointing_at(repository, run.id),
        history=history,
        mainline=mainline_entry,
        mainline_error=mainline_error,
        pr_url_template=pr_url_template,
        generated_at=datetime.now(UTC),
        generated_by=importlib.metadata.version("evaltrack"),
    )


def escape_json_for_html(json_text: str) -> str:
    """Rewrite `json_text` so that it can sit inside a `<script>` element, whatever it
    holds. The HTML parser ends the element at the first `</script` and knows no
    escaping inside it. So every `<`, `>` and `&` becomes its JSON escape, which decodes
    back to the same character. The two Unicode line terminators become escapes too,
    because some tools read a page as JavaScript source, where they end a line."""
    return (
        json_text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


@cache
def _load_template(path: Path) -> tuple[str, str]:
    """The template, split at its data slot. It is cached, because the built page does
    not change while the process runs. A failure is not cached, so a later build is
    picked up."""
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
    """The report as compact JSON. Each run is embedded as its run view. A value over
    `inline_limit` bytes is replaced by its preview and size, so the report of a large
    run stays small."""
    # The runs are dumped once, as their views, and not also whole.
    runs: dict[str, Any] = {
        "run": build_run_view(data.run, limit=inline_limit),
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
    """The report page for `data`, as one self-contained HTML document. A value over
    `inline_limit` bytes is left out. None embeds every value whole.

    Raises:
        FileNotFoundError: when the page template is not built.
        ValueError: when the template has no data slot, so it is not the template
            that this version writes into.
    """
    head, tail = _load_template(TEMPLATE_PATH)
    payload = escape_json_for_html(_dump_report_json(data, inline_limit=inline_limit))
    return f"{head}{_SLOT_OPEN}{payload}{_SLOT_CLOSE}{tail}"
