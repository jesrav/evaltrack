"""A single-file HTML report of a recorded run. The run is embedded in the page,
so it opens anywhere with no server and no network."""

import importlib.metadata
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel

from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import CorruptRecordError, RepositoryUnavailableError
from evaltrack.core.refs import BASELINE_REF, Ref
from evaltrack.core.run_record import RunRecord, dump_plain, dump_plain_json
from evaltrack.repositories import RunRepository
from evaltrack.views.mainline import (
    NoMainline,
    baseline_run_for,
    mainline_entry_for,
    run_history_over,
)
from evaltrack.views.models import MainlineEntry, RunHistory
from evaltrack.views.refs import refs_pointing_at
from evaltrack.views.run_view import INLINE_VALUE_BYTES, build_run_view

_logger = logging.getLogger(__name__)

# The frontend build writes this file.
TEMPLATE_PATH = Path(__file__).parent / "static" / "report.html"

# The empty script element in the template, as the build writes it. The report
# puts its JSON between these two tags, and the page reads the element by id.
_SLOT_OPEN = '<script type="application/json" id="evaltrack-data">'
_SLOT_CLOSE = "</script>"


class ReportData(BaseModel):
    """What a report embeds. It is the run, and what the mainline says about it.

    `via_ref` is the ref that named the run, when a ref did. `refs` are the refs that
    point at the run in its own repository. `history`, `mainline` and `baseline` come
    from the mainline. `baseline` is the run that the `baseline` ref points at, or None
    when there is no such run or it is the run itself. When the mainline was not read,
    `mainline_error` says why in a few fixed words, and those three are empty.
    """

    run: RunRecord
    via_ref: str | None = None
    baseline: RunRecord | None = None
    refs: list[Ref] = []
    history: RunHistory = RunHistory()
    mainline: MainlineEntry | None = None
    mainline_error: str | None = None
    pr_url_template: PrUrlTemplate | None = None
    generated_at: AwareDatetime
    generated_by: str


def collect_report_data(
    repository: RunRepository,
    run: RunRecord,
    *,
    remote: RunRepository | NoMainline,
    via_ref: str | None = None,
    pr_url_template: PrUrlTemplate | None = None,
) -> ReportData:
    """The data of a report for `run`, which `repository` holds. It is the run and what
    the mainline on `remote` says about it. That is the history, where the run landed
    on the mainline, and the `baseline` run to compare against. `via_ref` is the ref
    that named the run.

    If the mainline is missing, cannot be reached, or holds a `baseline` reflog that
    does not parse, the report has the run alone and `mainline_error` says why. A
    promoted run this evaltrack cannot read is left out of the history.
    """
    history = RunHistory()
    mainline_entry: MainlineEntry | None = None
    baseline: RunRecord | None = None
    mainline_error: str | None = None
    if isinstance(remote, NoMainline):
        mainline_error = remote
    else:
        try:
            # One read of the reflog serves all three. On a blob store each
            # read is a round trip.
            reflog = list(remote.get_reflog(BASELINE_REF))
            history = run_history_over(remote, reflog, viewed_run=run)
            mainline_entry = mainline_entry_for(reflog, run.id)
            baseline = baseline_run_for(remote, reflog, run_id=run.id)
        except (RepositoryUnavailableError, CorruptRecordError) as exc:
            # The detail stays out of the page, which is handed around.
            _logger.warning("the mainline was not read: %s", exc)
            history, mainline_entry, baseline = RunHistory(), None, None
            mainline_error = (
                NoMainline.REMOTE_NOT_REACHED
                if isinstance(exc, RepositoryUnavailableError)
                else NoMainline.REFLOG_DID_NOT_PARSE
            )
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


def _load_template(path: Path) -> tuple[str, str]:
    """The template, split at its data slot. Read on each call, so a running process
    picks up a new build."""
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
