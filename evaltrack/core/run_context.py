"""The commit, branch and labels a run is recorded against."""

from pydantic import BaseModel


class RunContext(BaseModel):
    """Metadata about the environment a run was recorded in. `worktree_dirty` is
    None when unknown."""

    commit: str | None = None
    worktree_dirty: bool | None = None
    labels: dict[str, str] = {}
