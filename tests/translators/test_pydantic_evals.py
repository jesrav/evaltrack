"""The pydantic-evals translator: what it reads out of a report, and the two
conventions of that runner it pins.

Everything runner-specific evaltrack knows lives behind this translator, so this
is where a pydantic-evals release that renames or reshapes something must fail.
"""

import asyncio
from collections import Counter
from typing import Any

import pytest
from pydantic_evals import (
    Case,
    Dataset,
    increment_eval_metric,
    set_eval_attribute,
)
from pydantic_evals.reporting import EvaluationReport

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound
from evaltrack.translators.pydantic_evals import (
    TRANSLATOR,
    raise_on_duplicate_case_ids,
)

from ..factories import (
    make_crash_report,
    make_eval_report,
    make_evaluator_failure,
    make_repeat_report,
    make_report_case,
    read_case_ids,
)
from .contract import assert_translator_contract


def _translate(report: EvaluationReport) -> EvalRound:
    return TRANSLATOR.translate(report)


def test_the_translator_holds_the_contract() -> None:
    """The properties every translator holds, checked against a real report.

    `tests/translators/contract.py` holds them. Each translator's module calls
    it with a result from its own runner.

    The report comes from a real `Dataset.evaluate` over several cases, not
    from `make_eval_report`. The contract's own docstring asks for one, and a
    hand-built single-case report leaves most of it vacuous: the collapsed-ids
    check needs two cases with different inputs, and the result-versus-error
    check needs a case that errored.
    """
    report = _evaluate(
        [
            Case(name="first", inputs="a", expected_output="a"),
            Case(name="second", inputs="b", expected_output="b"),
        ]
    )
    assert_translator_contract(TRANSLATOR, report, name="pydantic-evals")


class TestTranslate:
    def test_scores_and_assertions_carry_their_values_and_reasons(self) -> None:
        eval_round = _translate(
            make_eval_report(scores={"quality": 0.9}, assertions={"correct": True})
        )
        [attempt] = eval_round.attempts
        assert attempt.results["quality"].value == 0.9
        assert attempt.results["quality"].reason == "test"
        assert attempt.results["correct"].value is True

    def test_an_int_score_is_stored_as_its_float(self) -> None:
        """A `3` here and a `3` from another runner are the same stored score."""
        eval_round = _translate(make_eval_report(scores={"stars": 3}))
        value = eval_round.attempts[0].results["stars"].value
        assert type(value) is float and value == 3.0

    def test_a_score_carries_no_threshold(self) -> None:
        """pydantic-evals leaves a score ungated, so the only bar is the marker's."""
        eval_round = _translate(make_eval_report(scores={"quality": 0.9}))
        assert eval_round.attempts[0].results["quality"].bar is None

    def test_a_label_is_refused_rather_than_recorded(self) -> None:
        """A string-valued evaluator answers something evaltrack cannot gate
        on. Recording it would put an unjudged result in a run whose every
        other result decides the test."""
        with pytest.raises(EvalDefinitionError, match="grade answered with a label"):
            _translate(make_eval_report(labels={"grade": "B"}))

    def test_the_case_and_the_runner_are_named(self) -> None:
        eval_round = _translate(make_eval_report())
        assert read_case_ids(eval_round) == ["test_case"]
        assert eval_round.runner.name == "pydantic-evals"
        assert eval_round.runner.version is not None

    def test_inputs_and_output_travel_with_the_attempt(self) -> None:
        [attempt] = _translate(make_eval_report()).attempts
        assert (attempt.inputs, attempt.output) == ("test input", "test output")

    def test_repeated_cases_collapse_onto_one_case_id(self) -> None:
        """Under `repeat=N` pydantic-evals reports N run-indexed cases sharing a
        source name. They are one case with N attempts."""
        eval_round = _translate(make_repeat_report([True, False, True]))
        assert read_case_ids(eval_round) == ["c"]
        assert len(eval_round.attempts) == 3

    def test_a_crashed_task_becomes_an_errored_attempt(self) -> None:
        """pydantic-evals reports it apart from the cases. evaltrack keeps it
        as an attempt at the case, so the case is visible as errored rather than
        missing."""
        eval_round = _translate(make_crash_report(error_message="RuntimeError: boom"))
        [attempt] = eval_round.attempts
        assert attempt.case_id == "test_case"
        assert attempt.errored
        assert attempt.errors[0].message == "RuntimeError: boom"
        assert attempt.errors[0].evaluator is None, "the task raised, not an evaluator"

    def test_a_crashed_evaluator_is_named_on_the_attempt(self) -> None:
        case = make_report_case("c1", assertions={"x": True})
        case.evaluator_failures = [make_evaluator_failure()]
        [attempt] = _translate(EvaluationReport(name="r", cases=[case])).attempts
        assert attempt.errors[0].evaluator == "MyEvaluator"

    def test_a_report_level_evaluator_failure_belongs_to_the_round(self) -> None:
        report = EvaluationReport(
            name="r", cases=[], report_evaluator_failures=[make_evaluator_failure()]
        )
        assert _translate(report).errors[0].name == "MyEvaluator"

    def test_the_runners_own_case_names_are_the_ids(self) -> None:
        """A retry asks for cases by exactly the names the report used."""
        assert read_case_ids(_translate(make_eval_report())) == ["test_case"]


class TestTraceDetails:
    """pydantic-evals emits OpenTelemetry spans, and the recorded ids are how a
    case is found in Logfire (or any other collector). evaltrack records the
    ids and never the spans."""

    def test_trace_coordinates_are_recorded_as_details(self) -> None:
        report = make_eval_report()
        report.cases[0].trace_id = "abc123"
        report.cases[0].span_id = "def456"
        [attempt] = _translate(report).attempts
        assert attempt.details == {"trace_id": "abc123", "span_id": "def456"}

    def test_an_untraced_case_records_no_coordinates(self) -> None:
        assert _translate(make_eval_report()).attempts[0].details == {}

    def test_an_all_zero_id_is_recorded_as_absent(self) -> None:
        """OpenTelemetry spells "no span" as all zeroes, which is what an
        installed but unconfigured logfire reports. An id that addresses
        nothing is worse than none at all."""
        report = make_eval_report()
        report.cases[0].trace_id = "0" * 32
        report.cases[0].span_id = "0" * 16
        assert _translate(report).attempts[0].details == {}

    def test_a_crashed_case_keeps_its_coordinates(self) -> None:
        """A task that raised is the attempt whose trace is worth most."""
        report = make_crash_report()
        report.failures[0].trace_id = "abc123"
        assert _translate(report).attempts[0].details["trace_id"] == "abc123"

    def test_the_users_own_run_metadata_is_kept(self) -> None:
        """`evaluate(metadata=...)` is where a user records what a report has
        no field for. The clearest example is the dataset's name, which sits on
        the Dataset and not on its report."""
        report = make_eval_report()
        report.experiment_metadata = {"dataset": "qa-set"}
        assert _translate(report).details["experiment_metadata"] == {
            "dataset": "qa-set"
        }

    def test_no_run_metadata_adds_no_key(self) -> None:
        assert "experiment_metadata" not in _translate(make_eval_report()).details

    def test_the_whole_round_carries_its_own_coordinates(self) -> None:
        report = make_eval_report()
        report.trace_id = "run-trace"
        assert _translate(report).details["trace_id"] == "run-trace"


class TestExperimentName:
    """pydantic-evals calls a run an experiment and always names one, from
    `name=` or from the task function. evaltrack has no field of its own for
    that word, so the runner's stays in `details`."""

    def test_a_chosen_name_is_kept(self) -> None:
        report = make_eval_report(name="qa-experiment")
        assert _translate(report).details["experiment"] == "qa-experiment"

    def test_an_unnamed_run_keeps_the_task_functions_name(self) -> None:
        report = _evaluate([Case(name="c", inputs="a")])
        assert _translate(report).details["experiment"] == "_identity_task"


async def _identity(x: str) -> str:
    return x


def test_autoname_convention_matches_pydantic_evals() -> None:
    """The translator assumes that pydantic-evals names an unnamed case
    ``f"Case {i}"``, counted from 1, and keys it by that name. This test runs a
    real `Dataset.evaluate` and checks the recorded ids. If the framework
    renames its cases, this test fails before a rerun retries the wrong cases."""
    dataset = Dataset(
        name="d",
        cases=[Case(inputs="a"), Case(name="mid", inputs="b"), Case(inputs="c")],
    )
    report = asyncio.run(dataset.evaluate(_identity))
    assert sorted(read_case_ids(_translate(report))) == ["Case 1", "Case 3", "mid"], (
        "autonames are 1-based rows, counting named cases too"
    )


async def _identity_task(x: str) -> str:
    return x


def _evaluate(cases: list[Case], *, repeat: int = 1) -> EvaluationReport:
    """A real report for `cases`, so the ids are the ones pydantic-evals gives."""
    dataset = Dataset[str, str, Any](name="d", cases=cases)
    return asyncio.run(dataset.evaluate(_identity_task, progress=False, repeat=repeat))


async def _instrumented_task(x: str) -> str:
    increment_eval_metric("tokens", 42)
    set_eval_attribute("model", "gpt-4o-mini")
    return x


class TestCaseMetricsAndAttributes:
    """`increment_eval_metric` and `set_eval_attribute` are what a task calls to
    report what one case cost it: tokens, money, the model it reached for.
    evaltrack has no field of its own for any of that, so it stays in `details`.

    The report comes from a real run, because these are gathered from inside the
    task rather than passed to it.
    """

    def test_what_the_task_reported_is_recorded_as_details(self) -> None:
        dataset = Dataset[str, str, Any](name="d", cases=[Case(name="c", inputs="a")])
        report = asyncio.run(dataset.evaluate(_instrumented_task, progress=False))
        [attempt] = _translate(report).attempts
        assert attempt.details["metrics"] == {"tokens": 42}
        assert attempt.details["attributes"] == {"model": "gpt-4o-mini"}

    def test_a_task_that_reported_neither_adds_no_key(self) -> None:
        [attempt] = _translate(_evaluate([Case(name="c", inputs="a")])).attempts
        assert "metrics" not in attempt.details
        assert "attributes" not in attempt.details


class TestRaiseOnDuplicateCaseNames:
    """Rejects a report that names two cases alike, which happens when an
    explicit name repeats an unnamed case's positional auto-name.

    Read off the report rather than the dataset, because a translator only ever
    sees the report.
    """

    def test_all_unnamed_is_fine(self) -> None:
        # Distinct positional auto-names: "Case 1", "Case 2".
        raise_on_duplicate_case_ids(_evaluate([Case(inputs="a"), Case(inputs="b")]))

    def test_distinct_explicit_is_fine(self) -> None:
        raise_on_duplicate_case_ids(
            _evaluate([Case(name="foo", inputs="a"), Case(name="bar", inputs="b")])
        )

    def test_explicit_name_repeating_an_earlier_autoname_is_refused(self) -> None:
        # The unnamed case at position 1 auto-names "Case 1". pydantic-evals
        # lets the explicit "Case 1" through, so the report names two cases alike.
        with pytest.raises(EvalDefinitionError, match="duplicate.*'Case 1'"):
            raise_on_duplicate_case_ids(
                _evaluate([Case(inputs="a"), Case(name="Case 1", inputs="b")])
            )

    def test_explicit_name_matching_its_own_position_is_fine(self) -> None:
        raise_on_duplicate_case_ids(
            _evaluate([Case(name="Case 1", inputs="a"), Case(inputs="b")])
        )

    def test_a_bare_row_name_is_not_an_autoname(self) -> None:
        """The unnamed case at row 2 is named "Case 2", and "2" is another name."""
        raise_on_duplicate_case_ids(
            _evaluate([Case(name="2", inputs="a"), Case(inputs="b")])
        )

    def test_a_repeat_expansion_is_not_a_duplicate(self) -> None:
        """`repeat=N` reports N cases that share a source name under distinct
        names. That is one case with N attempts, not two cases named alike."""
        raise_on_duplicate_case_ids(make_repeat_report([True, True]))

    def test_a_real_repeat_run_yields_n_attempts_per_case_id(self) -> None:
        eval_round = _translate(
            _evaluate([Case(inputs="a"), Case(name="x", inputs="b")], repeat=2)
        )
        attempts_per_id = Counter(attempt.case_id for attempt in eval_round.attempts)
        assert attempts_per_id == {"Case 1": 2, "x": 2}

    def test_a_duplicate_under_repeat_is_still_refused(self) -> None:
        """Every expansion of the two cases named alike is named alike too."""
        report = _evaluate(
            [Case(inputs="a"), Case(name="Case 1", inputs="b")], repeat=2
        )
        with pytest.raises(EvalDefinitionError, match="duplicate"):
            raise_on_duplicate_case_ids(report)

    def test_translating_a_report_with_duplicate_names_is_refused(self) -> None:
        """The check runs where a translator can reach it, so the duplicate
        fails the test instead of merging two cases into one row."""
        report = _evaluate([Case(inputs="a"), Case(name="Case 1", inputs="b")])
        with pytest.raises(EvalDefinitionError, match="duplicate"):
            _translate(report)


class TestCaseIds:
    """A case is keyed by the name pydantic-evals reports, verbatim. An unnamed
    case is named `Case N` after its row, and that text is the id."""

    def test_an_unnamed_case_is_keyed_by_the_runners_name(self) -> None:
        eval_round = _translate(_evaluate([Case(inputs="a"), Case(inputs="b")]))
        assert read_case_ids(eval_round) == ["Case 1", "Case 2"]

    def test_names_the_user_chose_are_kept(self) -> None:
        eval_round = _translate(
            _evaluate([Case(name="alpha", inputs="a"), Case(name="beta", inputs="b")])
        )
        assert read_case_ids(eval_round) == ["alpha", "beta"]

    def test_a_mixed_dataset_keys_each_case_on_its_own_name(self) -> None:
        """The row counts named cases too, so the unnamed third case is `Case 3`."""
        eval_round = _translate(
            _evaluate(
                [
                    Case(name="alpha", inputs="a"),
                    Case(name="beta", inputs="b"),
                    Case(inputs="c"),
                ]
            )
        )
        assert read_case_ids(eval_round) == ["alpha", "beta", "Case 3"]

    def test_a_crashed_unnamed_case_keeps_its_name(self) -> None:
        """pydantic-evals reports a crashed case among the failures, after
        every case that produced a result. The id is read off the case's own
        name, so where the report puts the case changes nothing."""

        async def _crash_on_b(x: str) -> str:
            if x == "b":
                raise RuntimeError("boom")
            return x

        dataset = Dataset[str, str, Any](
            name="d", cases=[Case(inputs="a"), Case(inputs="b"), Case(inputs="c")]
        )
        report = asyncio.run(dataset.evaluate(_crash_on_b, progress=False))
        eval_round = _translate(report)
        assert sorted(
            (a.case_id, a.inputs, bool(a.errors)) for a in eval_round.attempts
        ) == [
            ("Case 1", "a", False),
            ("Case 2", "b", True),
            ("Case 3", "c", False),
        ]

    def test_a_repeat_over_unnamed_cases_keys_the_same_names(self) -> None:
        """The expansions of one row are one case, keyed the same whatever the
        repeat count, so `repeat=` never restarts a case's history."""
        once = _translate(_evaluate([Case(inputs="a"), Case(inputs="b")]))
        twice = _translate(_evaluate([Case(inputs="a"), Case(inputs="b")], repeat=2))
        assert read_case_ids(once) == read_case_ids(twice) == ["Case 1", "Case 2"]
        assert len(twice.attempts) == 4

    def test_a_repeat_over_named_cases_collapses_onto_the_name(self) -> None:
        """The convention the hand-built repeat reports encode, checked against
        the library.

        pydantic-evals expands `repeat=N` into cases named `"{name} [i/N]"` that
        share `source_case_name`, and the translator reads that field to fold
        them back. `factories.make_repeat_report` writes both by hand, so if
        upstream stopped setting `source_case_name` every test built on it would
        keep passing while real repeats split into N cases.
        """
        eval_round = _translate(_evaluate([Case(name="c", inputs="a")], repeat=3))
        assert read_case_ids(eval_round) == ["c"]
        assert len(eval_round.attempts) == 3

    def test_a_chosen_name_in_the_auto_format_is_kept_as_written(self) -> None:
        """A chosen `Case 7` is keyed `Case 7`. Nothing rewrites a name."""
        eval_round = _translate(
            _evaluate(
                [Case(name="Case 7", inputs="a"), Case(name="Case 9", inputs="b")]
            )
        )
        assert read_case_ids(eval_round) == ["Case 7", "Case 9"]
