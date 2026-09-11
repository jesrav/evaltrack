"""Ref types and the ref name rule. A ref is a mutable, lowercase name stored as
its reflog, whose newest entry is the current target. Only `baseline` is
reserved. Any other ref is a pull request when its reflog carries a PR number."""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, computed_field

from evaltrack.core.errors import InvalidIdentifierError
from evaltrack.core.run_record import RunId


class ReflogEntry(BaseModel):
    """One entry in a ref's reflog. It names the run the ref moved to, not the ref.

    Attributes:
        run_id: The run the ref moved to.
        moved_at: When the move was recorded.
        commit: The commit recorded for this move, if any.
        pr: The pull request number for the move, if any.
        title: The pull request title for the move, if any.
    """

    # Extras survive a read and write-back by an older evaltrack.
    model_config = ConfigDict(extra="allow")

    run_id: RunId
    moved_at: AwareDatetime
    commit: str | None = None
    pr: int | None = None
    title: str | None = None


def parse_reflog_entry(data: Any) -> ReflogEntry:
    """Parse one decoded reflog line into a `ReflogEntry`."""
    return ReflogEntry.model_validate(data)


class RefKind(StrEnum):
    """`BASELINE` is the ref the mainline is read from, `PR` a pull request's
    latest run, `OTHER` anything else."""

    BASELINE = "baseline"
    PR = "pr"
    OTHER = "other"


BASELINE_REF = "baseline"
"""The reserved ref name for the mainline, protected from accidental deletion."""


def ensure_lowercase_ref_name(name: str) -> str:
    """Check that a ref name is lowercase. Raises `InvalidIdentifierError` otherwise."""
    if name != name.lower():
        raise InvalidIdentifierError(
            f"{name!r} cannot be used as a ref name: ref names are lowercase, "
            f"so use {name.lower()!r} instead"
        )
    return name


class Ref(BaseModel):
    """A ref as a listing shows it. `tip` is None for an empty reflog, and such a
    ref stays listed so it can be deleted.

    `kind` derives from `name` and the tip's `pr`. `baseline` is always the ref
    the mainline is read from, and any other ref carrying a PR number is a pull
    request.
    """

    name: str
    tip: ReflogEntry | None = None

    @computed_field
    @property
    def kind(self) -> RefKind:
        if self.name == BASELINE_REF:
            return RefKind.BASELINE
        if self.tip is not None and self.tip.pr is not None:
            return RefKind.PR
        return RefKind.OTHER
