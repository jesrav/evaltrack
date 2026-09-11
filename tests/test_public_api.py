"""What `import evaltrack` exports, and what a caller can rely on it being.

These check the export list itself. An exception added to the hierarchy's side,
or one raised from a public method but never exported, is invisible to every
behavioural test in the suite and breaks a caller's `except`. A run model the
documented read path hands back but the root cannot name is invisible the same
way. Nothing else here catches either.
"""

import evaltrack
from evaltrack.core import errors

from .factories import make_case_run


def test_every_public_exception_can_be_caught_as_an_evaltrack_error() -> None:
    """One `except EvaltrackError` must catch everything evaltrack raises on
    purpose. A name ending in `Error` must also be catchable at all: a model
    with an exception-like name sits among the real exceptions and reads as
    something you can put in an `except`.

    The walk also fails on an `__all__` entry that no longer resolves.
    """
    exceptions = [
        obj
        for name in evaltrack.__all__
        if isinstance(obj := getattr(evaltrack, name), type)
        and issubclass(obj, BaseException)
    ]
    assert len(exceptions) > 1, "the walk over __all__ must find the real exceptions"
    for exc in exceptions:
        assert issubclass(exc, evaltrack.EvaltrackError), (
            f"one except EvaltrackError would miss {exc}"
        )
    for name in evaltrack.__all__:
        obj = getattr(evaltrack, name)
        if name.endswith("Error"):
            assert isinstance(obj, type) and issubclass(obj, BaseException), (
                f"{name} reads as an exception but cannot be caught as one"
            )


# Raised only by the repository's mutation methods, which no doc asks a user to
# call. They stay unexported so the export list matches what the docs teach; a
# caller who reaches the mutation API anyway still catches them as
# `EvaltrackError`. Move one out of here the day a doc teaches the call that
# raises it.
_UNEXPORTED_ERRORS = frozenset(
    {"BaselineRefProtectedError", "RefNotFoundError", "RunReferencedError"}
)


def test_every_exception_type_is_exported_from_the_package_root() -> None:
    """Callers name errors as `evaltrack.X`. An error that only
    `evaltrack.core.errors` can reach has no name they can use, so the only way
    to catch it is to import a private module.

    A new error is exported by default: it has to be named in
    `_UNEXPORTED_ERRORS` to stay private, so leaving one out is a decision
    rather than an oversight.
    """
    defined = {
        name
        for name, obj in vars(errors).items()
        if isinstance(obj, type) and issubclass(obj, errors.EvaltrackError)
    }
    missing = defined - set(evaltrack.__all__) - _UNEXPORTED_ERRORS
    assert not missing, f"reachable only as evaltrack.core.errors: {sorted(missing)}"


def test_no_unexported_error_is_also_exported() -> None:
    """The two lists must not drift into disagreeing about one name, and a
    stale entry left behind by a rename would otherwise sit here unnoticed."""
    defined = {
        name
        for name, obj in vars(errors).items()
        if isinstance(obj, type) and issubclass(obj, errors.EvaltrackError)
    }
    assert _UNEXPORTED_ERRORS <= defined, (
        f"names no longer defined in errors: {sorted(_UNEXPORTED_ERRORS - defined)}"
    )
    both = _UNEXPORTED_ERRORS & set(evaltrack.__all__)
    assert not both, f"held private and exported at once: {sorted(both)}"


def test_reading_a_run_hands_back_only_types_the_root_can_name() -> None:
    """The documented read path walks a run down to one attempt. A reader who
    cannot name what each step binds cannot annotate it, or write a function
    over it, without importing a private module."""
    run_record = make_case_run("readpath", "2024-01-01T00:00:00Z", "P")
    recorded_test = run_record.tests["t"]
    case = recorded_test.cases["c"]
    marker = recorded_test.marker
    assert marker is not None, "the factory records the marker's settings"
    for obj in (run_record, recorded_test, case, case.attempts[0], marker):
        name = type(obj).__name__
        assert name in evaltrack.__all__, f"the read path reaches {name}, unexported"
        assert getattr(evaltrack, name) is type(obj)


def test_no_export_is_named_so_pytest_would_collect_it() -> None:
    """evaltrack is a pytest plugin, so its exports are imported into test files.

    pytest collects any class named `Test*`, and warns that it cannot when the
    class takes `__init__`. Under `filterwarnings = error` that warning ends the
    whole session, so a `Test`-prefixed export breaks every user who imports it.
    """
    collected = [name for name in evaltrack.__all__ if name.startswith("Test")]
    assert not collected, (
        f"pytest would try to collect {collected} out of a user's test file"
    )
