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
    """What a report embeds. It is the run, and what the mainline says about it.

    `via_ref` is the ref that the report was asked for by, if any. `refs` are the refs
    that point at the run in its own repository. `history`, `mainline` and `baseline`
    come from the mainline. `baseline` is the run that the `baseline` ref points at. It
    is None when there is no such run, or when it is the run itself. When the mainline
    cannot be read, `mainline_error` says why and those three are empty.
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
