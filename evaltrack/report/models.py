"""What a report embeds, and where its mainline is read from.
`frontend/src/reportData.ts` mirrors `ReportData`."""

from dataclasses import dataclass

from pydantic import AwareDatetime, BaseModel

from evaltrack.config import PrUrlTemplate
from evaltrack.core.refs import Ref
from evaltrack.core.run_record import RunRecord
from evaltrack.repositories import RunRepository
from evaltrack.views.models import MainlineEntry, RunHistory


@dataclass(frozen=True)
class Mainline:
    """The repository the mainline is read from, or None and the reason there
    is none."""

    repository: RunRepository | None
    error: str | None = None


class ReportData(BaseModel):
    """What the single-file report embeds: the run, and what the mainline says
    about it. The shapes are the ones the API serves, so the page reads them
    as the dashboard does.

    `via_ref` is the ref the report was asked for by, when it was asked for
    by one. `refs` are the refs pointing at the run in its own repository. `history`,
    `mainline` and `baseline` come from the mainline. `baseline` is the run
    the `baseline` ref points at, for the page to compare against, and None
    when there is none or it is the run itself. `mainline_error` says why,
    when the mainline could not be read, and the three are then empty.
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
