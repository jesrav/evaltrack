"""`Ref` and its derived `RefKind`, which comes from the ref name and the
tip entry's PR number."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evaltrack.core.errors import InvalidIdentifierError
from evaltrack.core.refs import (
    Ref,
    RefKind,
    ReflogEntry,
    ensure_lowercase_ref_name,
    parse_reflog_entry,
)
from evaltrack.core.run_record import RUN_SCHEMA_VERSION

from ..factories import make_run_id


def make_tip(
    *,
    commit: str | None = None,
    pr: int | None = None,
    title: str | None = None,
) -> ReflogEntry:
    """A tip reflog entry that points at some run."""
    return ReflogEntry(
        run_id=make_run_id("base"),
        moved_at=datetime(2026, 1, 1, tzinfo=UTC),
        commit=commit,
        pr=pr,
        title=title,
    )


def _make_entry_line(moved_at: str) -> str:
    return (
        f'{{"run_id": "{make_run_id("x")}", "moved_at": "{moved_at}",'
        f' "run_schema_version": {RUN_SCHEMA_VERSION}}}'
    )


def test_moved_at_round_trips_through_the_stored_string_format() -> None:
    """Reflogs written when `moved_at` was a string hold ISO 8601 values.
    They must load as the same instant, and a new entry must store one back."""
    entry = ReflogEntry.model_validate_json(
        _make_entry_line("2026-01-01T00:00:00+00:00")
    )
    assert entry.moved_at == datetime(2026, 1, 1, tzinfo=UTC)
    stored = json.loads(entry.model_dump_json())["moved_at"]
    assert isinstance(stored, str)
    assert datetime.fromisoformat(stored) == entry.moved_at


def test_moved_at_refuses_a_time_with_no_zone() -> None:
    """Entries are written by whichever machine pushed, and read anywhere, so a
    local wall clock reading would order the reflog wrong."""
    with pytest.raises(ValidationError):
        ReflogEntry.model_validate_json(_make_entry_line("2026-01-01T00:00:00"))


def test_baseline_is_classified_by_its_reserved_name() -> None:
    ref = Ref(name="baseline")
    assert ref.kind is RefKind.BASELINE


def test_pr_classification_is_name_independent() -> None:
    """The name carries no meaning beyond `baseline`. Any ref whose tip stores
    a PR number is a pull request, whatever its name."""
    ref = Ref(name="alice/login-fix", tip=make_tip(pr=42))
    assert ref.kind is RefKind.PR


def test_a_ref_without_a_pr_number_is_other() -> None:
    """A ref named `pr/...` with no stored PR number is a plain ref. The number
    comes from `push --pr`, not from the name."""
    ref = Ref(name="pr/feature-x", tip=make_tip())
    assert ref.kind is RefKind.OTHER


def test_baseline_stays_the_mainline_with_a_pr_number() -> None:
    """A promote copies the merged PR's number onto the `baseline` entry, so
    the reserved name must win over the stored number."""
    ref = Ref(name="baseline", tip=make_tip(pr=42))
    assert ref.kind is RefKind.BASELINE


def test_a_ref_with_no_history_points_at_nothing() -> None:
    """A ref creation that failed part way leaves a ref whose reflog is empty.
    The model still represents it, with no tip, so it can be listed and
    deleted."""
    ref = Ref(name="pr/13")
    assert ref.kind is RefKind.OTHER


@pytest.mark.parametrize("name", ["baseline", "pr/123"])
def test_lowercase_ref_names_are_accepted(name: str) -> None:
    assert ensure_lowercase_ref_name(name) == name


@pytest.mark.parametrize("name", ["Baseline", "BASELINE", "pr/Feature", "Éclair"])
def test_a_non_lowercase_ref_name_is_refused(name: str) -> None:
    """Refused, not lowercased. A caller who asked for `Release` must not end up
    writing to a ref called `release` without being told."""
    with pytest.raises(InvalidIdentifierError) as excinfo:
        ensure_lowercase_ref_name(name)
    message = str(excinfo.value)
    assert name in message
    assert name.lower() in message, "the message offers the name to use instead"


def test_a_reflog_entry_refuses_a_run_id_that_is_not_a_ulid() -> None:
    """The referenced-run guard compares against this run id, so the type must
    refuse a spelling that those comparisons would miss."""
    with pytest.raises(ValidationError):
        ReflogEntry(
            run_id=make_run_id("x").lower(),
            moved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def _make_entry_json(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "run_id": make_run_id("a"),
        "moved_at": "2026-01-01T00:00:00Z",
        "commit": "abc",
    }
    entry.update(overrides)
    return entry


def test_a_reflog_entry_keeps_a_field_it_does_not_declare() -> None:
    """A reflog is append-only, so a newer writer's field is never overwritten
    on disk. This version keeps the field, so it can show it and pass it on
    instead of dropping it."""
    entry = parse_reflog_entry(_make_entry_json(future_field="matters"))
    assert json.loads(entry.model_dump_json())["future_field"] == "matters"
