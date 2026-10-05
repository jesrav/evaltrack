"""What only the dashboard's API returns. `frontend/src/types.ts` mirrors these."""

from dataclasses import dataclass

from pydantic import BaseModel

from evaltrack.config import PrUrlTemplate, RepositoryRole
from evaltrack.core.refs import Ref, ReflogEntry
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
