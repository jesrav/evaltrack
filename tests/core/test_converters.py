"""A value of a registered type is stored as its converter returns it.

Registration is process-wide, so every test here works on its own copy of it.
"""

import logging
from typing import Any

import pytest
from pydantic import BaseModel

from evaltrack.converters import register
from evaltrack.core import converters as registry
from evaltrack.core.eval_round import RoundAttempt
from evaltrack.core.user_values import UserValue

HEAVY = "tests.core.test_converters.Heavy"


class Heavy:
    """An object like the result of an agent framework, with the answer and
    much more."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.state = "x" * 10_000


class SpecialHeavy(Heavy):
    pass


class _Holder(BaseModel):
    value: UserValue


@pytest.fixture(autouse=True)
def _own_registry(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give each test its own copy of the process-wide registry."""
    monkeypatch.setattr(registry, "_registry", dict(registry._registry))  # pyright: ignore[reportPrivateUsage]


def _answer_of(value: Any) -> Any:
    return {"answer": value.answer}


def test_a_registered_type_is_stored_as_the_converter_returns_it() -> None:
    register("heavy", native_types=(HEAVY,), convert=_answer_of)
    attempt = RoundAttempt(case_id="c", output=Heavy("yes"))
    assert attempt.output == {"answer": "yes"}


def test_a_subclass_of_a_registered_type_is_converted() -> None:
    register("heavy", native_types=(HEAVY,), convert=_answer_of)
    assert _Holder(value=SpecialHeavy("yes")).value == {"answer": "yes"}


def test_an_unregistered_type_is_left_alone() -> None:
    register("heavy", native_types=(HEAVY,), convert=_answer_of)
    value = {"answer": "yes"}
    assert _Holder(value=value).value is value


def test_what_a_converter_returns_still_degrades() -> None:
    register("heavy", native_types=(HEAVY,), convert=lambda v: {"blob": b"\xff"})
    stored = _Holder(value=Heavy("yes")).value
    assert "$binary" in stored["blob"], "undumpable bytes were not fingerprinted"


def test_a_converter_that_raises_leaves_the_value_as_it_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(value: Any) -> Any:
        raise AttributeError("renamed in a later release")

    register("heavy", native_types=(HEAVY,), convert=broken)
    value = Heavy("yes")
    with caplog.at_level(logging.WARNING):
        assert _Holder(value=value).value is value
    assert "'heavy' converter raised" in caplog.text


def test_registering_again_under_the_same_name_replaces() -> None:
    register("heavy", native_types=(HEAVY,), convert=_answer_of)
    register("heavy", native_types=(HEAVY,), convert=lambda v: "replaced")
    assert _Holder(value=Heavy("yes")).value == "replaced"


def test_a_second_name_cannot_claim_a_registered_type() -> None:
    register("heavy", native_types=(HEAVY,), convert=_answer_of)
    with pytest.raises(ValueError, match="already claims"):
        register("other", native_types=(HEAVY,), convert=_answer_of)


def test_a_converter_needs_a_type() -> None:
    with pytest.raises(ValueError, match="names no native_types"):
        register("heavy", native_types=(), convert=_answer_of)
