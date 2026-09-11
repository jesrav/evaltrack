"""The contract every translator holds, whichever runner it reads.

Each translator's own test module calls `assert_translator_contract` with a
result from a real run of its runner. It lives here rather than as a
parametrized fixture because a translator can only be exercised where its runner
is installed, and several of them are not installed by default.

These are the properties that make a translated round usable: it can be
dispatched to, gated, stored, and compared with the same round next time.
"""

import math
from typing import Any

from evaltrack.core.eval_round import EvalRound
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_record import dump_run_json
from evaltrack.translators import Translator, find_translator

from ..factories import read_case_ids


def _assert_dispatches(translator: Translator, result: Any) -> None:
    assert find_translator(result) is translator, (
        f"a {type(result).__qualname__} dispatched to another translator: the "
        "native_types registered for this one are wrong or another translator "
        "claims the same type"
    )


def _assert_cases_are_identified(eval_round: EvalRound) -> None:
    for attempt in eval_round.attempts:
        assert attempt.case_id, (
            "every attempt needs a case id: it keys the case in the stored run "
            "and in its history across runs"
        )
    by_input: dict[str, set[str]] = {}
    for attempt in eval_round.attempts:
        by_input.setdefault(repr(attempt.inputs), set()).add(attempt.case_id)
    collapsed = {
        case_id
        for ids in by_input.values()
        for case_id in ids
        if sum(case_id in other for other in by_input.values()) > 1
    }
    assert not collapsed, (
        f"cases with different inputs share the ids {sorted(collapsed)}, so they "
        "merge into one row and one history. Repeats of a single case may share "
        "an id; distinct cases may not"
    )


def _assert_results_are_storable(eval_round: EvalRound) -> None:
    for attempt in eval_round.attempts:
        for name, result in attempt.results.items():
            assert not result.is_score or math.isfinite(float(result.value)), (
                f"{name} on {attempt.case_id} is not a finite number. A runner "
                "that reports a non-value as NaN needs it recorded as an error "
                "on the attempt, not as a score"
            )
        errored = {error.evaluator for error in attempt.errors}
        both = {name for name in errored if name is not None} & attempt.results.keys()
        assert not both, (
            f"{attempt.case_id} reports {sorted(both)} as both a result and an "
            "error: an evaluator either produced a verdict or it did not"
        )


def _assert_translation_is_deterministic(translator: Translator, result: Any) -> None:
    """The same result must translate the same way twice.

    A translator that keys a case on object identity, insertion order or a
    random value would give the same case a different id on the next run, and
    silently restart its pass-rate history.
    """
    first = translator.translate(result)
    second = translator.translate(result)
    assert read_case_ids(first) == read_case_ids(second), (
        "translating the same result twice produced different case ids"
    )


def _assert_it_records(eval_round: EvalRound) -> None:
    """The round survives the projection into a stored run.

    A translator can produce something the gate accepts but the recorder cannot
    store, and that failure would otherwise land at session end with a whole
    suite's results already spent.
    """
    recorder = EvalRecorder()
    recorder.add_round("tests/test_contract.py::test_eval", eval_round)
    dump_run_json(recorder.to_run_record())


def assert_translator_contract(
    translator: Translator, result: Any, *, name: str
) -> None:
    """Check `translator` against a real result from its runner.

    Args:
        translator: The translator under test, as registered.
        result: What its runner returned from a real eval. A hand-built
            stand-in would not exercise the shapes that matter.
        name: What the runner is called. Every round the translator
            builds must report it.
    """
    _assert_dispatches(translator, result)
    eval_round = translator.translate(result)
    assert isinstance(eval_round, EvalRound)
    assert eval_round.runner.name == name, "the round must name the runner it came from"
    assert eval_round.attempts, "a translated round with no attempts records nothing"
    _assert_cases_are_identified(eval_round)
    _assert_results_are_storable(eval_round)
    _assert_translation_is_deterministic(translator, result)
    _assert_it_records(eval_round)
