"""The refs of a repository, each with its tip, and the refs that point at a run."""

import logging
from collections.abc import Iterator
from typing import NamedTuple

from evaltrack.core.errors import CorruptRecordError, InvalidIdentifierError
from evaltrack.core.refs import Ref, ReflogEntry
from evaltrack.repositories import RunRepository

_logger = logging.getLogger(__name__)


class RefTip(NamedTuple):
    """A ref and its tip. `tip` is None with `error` set when the ref's history
    cannot be read, and None alone when the ref points at nothing."""

    name: str
    tip: ReflogEntry | None
    error: str | None


def iter_ref_tips(repo: RunRepository) -> Iterator[RefTip]:
    """Every ref by name with its tip. A ref whose history cannot be read is
    still yielded, logged, so a caller can show or repair it."""
    for name in sorted(repo.list_refs()):
        try:
            yield RefTip(name, repo.get_ref(name), None)
        except (CorruptRecordError, InvalidIdentifierError) as exc:
            _logger.warning("ref %s has an unreadable history: %s", name, exc)
            yield RefTip(name, None, str(exc))


def refs_pointing_at(repo: RunRepository, run_id: str) -> list[Ref]:
    """The refs whose tip is `run_id`, by name. A ref whose history cannot be
    read is left out, since its tip is unknown, not absent."""
    return [
        Ref(name=name, tip=tip)
        for name, tip, _ in iter_ref_tips(repo)
        if tip is not None and tip.run_id == run_id
    ]
