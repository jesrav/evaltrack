"""What the API returns. `frontend/src/types.ts` mirrors these."""

from dataclasses import dataclass

from pydantic import AwareDatetime, BaseModel, ConfigDict

from evaltrack.config import PrUrlTemplate, RepositoryRole
from evaltrack.core.refs import Ref, ReflogEntry
from evaltrack.core.run_record import RunRecord
from evaltrack.history.reliability import CaseReliability
from evaltrack.history.score_history import ScoreHistory
from evaltrack.repositories import RunRepository, RunSummary


@dataclass(frozen=True)
class MountedRepository:
    """A mounted repository. `role` decides which mount supplies the mainline."""

    url: str
    repository: RunRepository
    role: RepositoryRole


class RepositoryInfo(BaseModel):
    slug: str
    url: str
    role: RepositoryRole


class ProjectConfig(BaseModel):
    """Project-level settings, not tied to any one repository."""

    pr_url_template: PrUrlTemplate | None = None


class RefListing(Ref):
    """A ref plus the summary of the run it points at, None when that run cannot be
    read. `error` says why the ref's own history could not be read, and `tip` is
    then None without meaning the ref points at nothing."""

    tip_run: RunSummary | None = None
    error: str | None = None


class ReflogListing(ReflogEntry):
    """A reflog entry plus the summary of the run it moved to."""

    run: RunSummary | None = None


class MainlineEntry(BaseModel):
    """Where a run ended up on the mainline, as the newest `baseline` reflog entry
    pointing at it.

    The run's own eval-time commit cannot give this, because a squash merge
    leaves that commit off main.
    """

    commit: str | None = None
    pr: int | None = None
    title: str | None = None
    moved_at: AwareDatetime


class RunHistory(BaseModel):
    """A repository's cross-run history: how reliably each case has passed, and
    how each of its scores has moved.

    The two are served together because they are measured over one read of the
    mainline. A request for each reads every run body in the window twice.

    `reliability` is keyed by test, then by case. `score_history` is keyed by
    test and holds one history per score. Both are empty when there is no
    mainline history to measure over.
    """

    reliability: dict[str, dict[str, CaseReliability]] = {}
    score_history: dict[str, list[ScoreHistory]] = {}


class ReportData(BaseModel):
    """What the single-file report embeds: the same shapes the API serves, so
    the page reads them as the dashboard does.

    `via` and `against_via` name the ref each run was reached by, when it was
    one. `refs` are the refs pointing at the run in its own repository.
    `history` and `mainline` are measured over the mainline the generator
    chose. `history` is empty for a comparison, which does not render it, and
    when the mainline could not be read, which `history_error` then says.
    """

    # The top-level serializer decides how a non-finite score is written, and
    # the API writes it as a string.
    model_config = ConfigDict(ser_json_inf_nan="strings")

    run: RunRecord
    via: str | None = None
    against: RunRecord | None = None
    against_via: str | None = None
    refs: list[Ref] = []
    history: RunHistory = RunHistory()
    mainline: MainlineEntry | None = None
    history_error: str | None = None
    pr_url_template: PrUrlTemplate | None = None
    generated_at: AwareDatetime
    generated_by: str
