"""The registry: what is registered, how a result finds its translator, and how
a translator is replaced.

Registration is process-wide, so every test here works on its own copy of it.
"""

from typing import Any

import pytest

from evaltrack import translators as registry
from evaltrack.core.errors import TranslatorNotFoundError
from evaltrack.core.eval_round import EvalRound
from evaltrack.translators import (
    Translator,
    deepeval,
    find_translator,
    pydantic_evals,
    register,
)

from ..factories import make_eval_report, make_round

FAKE_RESULT = "tests.translators.test_registry.FakeResult"
SPECIAL_RESULT = "tests.translators.test_registry.SpecialResult"
PYDANTIC_EVALS_REPORT = "pydantic_evals.reporting.EvaluationReport"


class FakeResult:
    """What a runner unknown to evaltrack hands back."""


class SpecialResult(FakeResult):
    """What the same runner hands back for a richer eval."""


class FakeTranslator:
    """Reads `FakeResult` the way a real translator reads its runner."""

    def translate(self, result: Any) -> EvalRound:
        return make_round()


@pytest.fixture(autouse=True)
def _own_registry(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give each test its own copy of the process-wide registry."""
    monkeypatch.setattr(registry, "_registry", dict(registry._registry))  # pyright: ignore[reportPrivateUsage]


def test_the_shipped_translators_dispatch_by_result_type() -> None:
    """The DeepEval entry claims a result type by its dotted path alone. A fake
    type at that path is enough to find the translator, which imports no
    DeepEval."""
    assert find_translator(make_eval_report()) is pydantic_evals.TRANSLATOR
    result_type = type(
        "EvaluationResult", (), {"__module__": "deepeval.evaluate.types"}
    )
    assert find_translator(result_type()) is deepeval.TRANSLATOR


def test_a_registered_translator_is_found() -> None:
    translator: Translator = FakeTranslator()
    register("temp", native_types=(FAKE_RESULT,), load=lambda: translator)
    assert find_translator(FakeResult()) is translator


def test_a_translator_is_built_once() -> None:
    """`load` is often the translator class itself, so a lookup per round would
    rebuild it every round, and every lookup must answer with the one instance
    the translator contract checks for."""
    register("temp", native_types=(FAKE_RESULT,), load=FakeTranslator)
    assert find_translator(FakeResult()) is find_translator(FakeResult())


def test_registering_no_native_types_is_refused() -> None:
    """No result can dispatch to it, so the runner looks supported until a
    lookup finds nothing."""
    with pytest.raises(ValueError, match="names no native_types"):
        register("typeless-runner", native_types=(), load=lambda: FakeTranslator())
    with pytest.raises(TranslatorNotFoundError):
        find_translator(FakeResult())


def test_a_later_register_under_a_name_replaces_the_earlier_one() -> None:
    """The way to replace a translator evaltrack ships."""
    mine = FakeTranslator()
    register("pydantic-evals", native_types=(PYDANTIC_EVALS_REPORT,), load=lambda: mine)
    assert find_translator(make_eval_report()) is mine


def test_a_replacement_wins_over_an_already_built_translator() -> None:
    """The translator built for the earlier registration must not outlive it."""
    register("temp", native_types=(FAKE_RESULT,), load=FakeTranslator)
    find_translator(FakeResult())
    mine = FakeTranslator()
    register("temp", native_types=(FAKE_RESULT,), load=lambda: mine)
    assert find_translator(FakeResult()) is mine


def test_a_new_name_claiming_a_taken_native_type_is_refused() -> None:
    """Registering under a fresh name never won the dispatch, since the shipped
    entry comes first, so the refusal points at the name that does work."""
    with pytest.raises(ValueError) as refusal:
        register(
            "my-pydantic-evals",
            native_types=(PYDANTIC_EVALS_REPORT,),
            load=lambda: FakeTranslator(),
        )
    message = str(refusal.value)
    assert "'my-pydantic-evals'" in message
    assert "'pydantic-evals'" in message
    assert PYDANTIC_EVALS_REPORT in message
    assert find_translator(make_eval_report()) is pydantic_evals.TRANSLATOR


def test_registering_the_same_name_twice_does_not_raise() -> None:
    """A conftest.py imported twice registers twice, and reclaiming its own
    types is not a clash."""
    translator: Translator = FakeTranslator()
    register("temp", native_types=(FAKE_RESULT,), load=lambda: translator)
    register("temp", native_types=(FAKE_RESULT,), load=lambda: translator)
    assert find_translator(FakeResult()) is translator


def test_a_claim_on_a_subclass_answers_for_that_subclass() -> None:
    """Dispatch tries a result's own type first and its base classes after, so
    the specific claim wins."""
    general = FakeTranslator()
    special = FakeTranslator()
    register("general", native_types=(FAKE_RESULT,), load=lambda: general)
    register("special", native_types=(SPECIAL_RESULT,), load=lambda: special)
    assert find_translator(SpecialResult()) is special
    assert find_translator(FakeResult()) is general
