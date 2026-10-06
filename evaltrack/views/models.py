"""The shapes of what the mainline says about a run. `frontend/src/types.ts`
mirrors these."""

from pydantic import AwareDatetime, BaseModel

from evaltrack.history.reliability import CaseReliability
from evaltrack.history.score_history import ScoreHistory


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
