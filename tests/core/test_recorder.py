"""`EvalRecorder`: what it records per test, and how it rolls attempts up."""

import asyncio
import itertools
import warnings

import pytest
from pydantic import BaseModel, Field
from pydantic_evals import Case, Dataset

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import AttemptErrorRecord, RoundErrorRecord
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_context import RunContext
from evaltrack.core.run_record import (
    CaseOutcome,
    MarkerSettings,
    RecordedBy,
    dump_run_json,
    parse_run_json,
)

from ..factories import (
    make_attempt,
    make_crash_round,
    make_repeat_round,
    make_round,
    translate_report,
)


def test_recorder_uses_provided_run_id() -> None:
    rec = EvalRecorder(run_id="01J9PINNED")
    assert rec.id == "01J9PINNED"


def test_recorder_carries_context_into_run() -> None:
    ctx = RunContext(commit="abc", labels={"branch": "main"})
    run = EvalRecorder(ctx).to_run_record()
    assert run.commit == "abc"
    assert run.labels == {"branch": "main"}


def test_recorder_stamps_installed_tool_versions() -> None:
    """A run says which evaltrack and pydantic-evals produced it, so the raw
    reports it carries stay interpretable later."""
    run = EvalRecorder().to_run_record()
    assert run.recorded_by == RecordedBy.from_installed()


# add_round


def test_add_round_records_the_eval_under_the_test() -> None:
    rec = EvalRecorder()
    rec.add_round("test_foo", make_round())
    test = rec.to_run_record().tests["test_foo"]
    assert "test_case" in test.cases


def test_second_top_level_add_round_raises() -> None:
    """A test records one eval. A silent second one makes eval identity depend
    on call order. A whole-test rerun's second eval has no one verdict with the
    first. The recorder refuses either one and points at the marker and the
    rerun plugin. The refused eval records nothing, so the rounds already paid
    for survive."""
    rec = EvalRecorder()
    rec.add_round("test_foo", make_round())
    with pytest.raises(EvalDefinitionError, match="already recorded an eval") as caught:
        rec.add_round("test_foo", make_round())
    assert "flakiness.md#rerun-on-failure-flake_reruns" in str(caught.value)
    case = rec.to_run_record().tests["test_foo"].cases["test_case"]
    assert case.clean_attempts == 1


def test_add_round_in_different_tests_no_conflict() -> None:
    rec = EvalRecorder()
    rec.add_round("test_one", make_round())
    rec.add_round("test_two", make_round())
    run = rec.to_run_record()
    assert run.tests["test_one"].marker is not None
    assert run.tests["test_two"].marker is not None


# case-centric build


def test_to_run_record_projects_cases() -> None:
    rec = EvalRecorder()
    rec.add_round("test_x", make_round())  # passing case
    run = rec.to_run_record()

    test = run.tests["test_x"]
    case = test.cases["test_case"]
    assert case.outcome == "passed"
    assert case.passed_attempts == 1
    assert case.clean_attempts == 1
    assert [a.output for a in case.attempts] == ["test output"]


# native repeat (evaluate(repeat=N)): collapsed into one case, all must pass


def test_repeat_collapses_into_one_case_with_n_attempts() -> None:
    rec = EvalRecorder()
    three = MarkerSettings(repeats=3)
    rec.add_round("t", make_repeat_round([True, False, True]), settings=three)
    rec.add_round("ok", make_repeat_round([True, True, True]), settings=three)
    run = rec.to_run_record()
    case = run.tests["t"].cases["c"]
    assert case.clean_attempts == 3
    assert case.passed_attempts == 2
    assert [a.output for a in case.attempts] == ["output 1", "output 2", "output 3"]
    assert case.outcome != "passed", (
        "one failed attempt fails the case, no in-run tolerance"
    )
    assert run.tests["ok"].cases["c"].outcome == "passed"
    assert case.attempts[0].outcome == "passed", (
        "the case records how each attempt went, not just eventual success"
    )


def test_building_a_run_never_warns() -> None:
    """The build runs at the end of a session, where `filterwarnings = error`
    would turn a warning into a crash and lose the whole recorded run. Anything
    worth warning about is warned while the test itself runs."""
    rec = EvalRecorder()
    rec.add_round("t", make_round())
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        run = rec.to_run_record()
    case = run.tests["t"].cases["test_case"]
    assert set(case.attempts[0].results) == {"quality", "passed"}


def test_crashed_evaluator_attempt_is_errored_not_passed() -> None:
    """An attempt with a crashed evaluator must roll up as a failure in every
    count the case carries. Not all of its checks ran, so a case that errored
    the test must never record a passing verdict for a reader to act on.
    """
    eval_round = make_round(
        attempts=[
            make_attempt(
                errors=[AttemptErrorRecord(message="judge API down", evaluator="Judge")]
            )
        ]
    )
    rec = EvalRecorder()
    rec.add_round("t", eval_round)
    built = rec.to_run_record().tests["t"]
    recorded = built.cases["test_case"]
    attempt = recorded.attempts[0]
    assert attempt.outcome == "errored"
    assert [(e.evaluator, e.message) for e in attempt.errors] == [
        ("Judge", "judge API down")
    ], (
        "the evaluator's name and message travel on the attempt, so the reason "
        "is readable without the raw reports"
    )
    assert recorded.outcome == "errored"
    assert recorded.passed_attempts == 0
    assert recorded.clean_attempts == 0, "an errored attempt reaches no verdict"
    assert recorded.errored_attempts == 1


def test_crashed_task_attempt_is_recorded() -> None:
    """A case whose only attempt crashed must still be recorded. A runner that
    keeps a raised task out of its cases (pydantic-evals does) would otherwise
    leave no record of the crash."""
    rec = EvalRecorder()
    rec.add_round("t", make_crash_round(error_message="RuntimeError: no API key"))
    built = rec.to_run_record().tests["t"]
    recorded = built.cases["test_case"]
    assert recorded.clean_attempts == 0, "no attempt reached a verdict"
    assert recorded.passed_attempts == 0
    assert recorded.errored_attempts == 1
    assert recorded.outcome == "errored"
    attempt = recorded.attempts[0]
    assert attempt.outcome == "errored"
    assert [(e.evaluator, e.message) for e in attempt.errors] == [
        (None, "RuntimeError: no API key")
    ], "no evaluator on the record is what marks the task as the thing that raised"
    assert recorded.inputs == "test input", (
        "inputs come off the errored attempt, keeping the row identifiable"
    )


def test_a_huge_error_message_is_capped_and_every_error_is_kept() -> None:
    """A dumped payload in one message must not bloat the run. The cap is per
    message, so a second failure is stored whole beside the capped one."""
    eval_round = make_round(
        attempts=[
            make_attempt(
                errors=[
                    AttemptErrorRecord(message="x" * 2000, evaluator="Judge"),
                    AttemptErrorRecord(message="also down", evaluator="Other"),
                ]
            )
        ]
    )
    rec = EvalRecorder()
    rec.add_round("t", eval_round)
    built = rec.to_run_record().tests["t"]
    capped, whole = built.cases["test_case"].attempts[0].errors
    assert len(capped.message) == 500, "a message is capped at 500 characters"
    assert capped.message.endswith("…")
    assert whole.message == "also down", "the cap applies per message, not per attempt"


def test_clean_attempt_records_no_error() -> None:
    """An error on the record means the attempt errored. A failed attempt that
    reached a verdict must carry none, or a reader sees an error that never
    happened."""
    eval_round = make_round(assertions={"passed": False})
    rec = EvalRecorder()
    rec.add_round("t", eval_round)
    built = rec.to_run_record().tests["t"]
    attempt = built.cases["test_case"].attempts[0]
    assert attempt.outcome == "failed"
    assert attempt.errors == []


def test_round_errors_reach_the_stored_run_and_load_back() -> None:
    """A round can fail as a whole while every case it did produce passed: the
    runner was cancelled, or its harness raised. The run records that the test
    errored, so it has to record what errored it, or a reader is left with a
    verdict and no reason anywhere under it."""
    rec = EvalRecorder()
    rec.add_round(
        "t",
        make_round(
            errors=[RoundErrorRecord(name="cancelled", message="killed at 3 of 10")]
        ),
    )
    rec.set_test_outcome("t", "errored")

    restored = parse_run_json(dump_run_json(rec.to_run_record()))
    test = restored.tests["t"]
    assert test.outcome == "errored"
    assert [(e.name, e.message) for e in test.errors] == [
        ("cancelled", "killed at 3 of 10")
    ]


def test_each_round_contributes_its_own_errors() -> None:
    """A rerun is another round of the same eval, and each round fails for its
    own reason. Keeping one leaves a reader chasing a failure the run never
    names."""
    rec = EvalRecorder()
    rec.add_round(
        "t", make_round(errors=[RoundErrorRecord(name="cancelled", message="one")])
    )
    rec.add_round(
        "t",
        make_round(errors=[RoundErrorRecord(name="error", message="two")]),
        continuing=True,
    )

    test = rec.to_run_record().tests["t"]
    assert [(e.name, e.message) for e in test.errors] == [
        ("cancelled", "one"),
        ("error", "two"),
    ]


def test_attempt_keeps_every_error_through_a_round_trip() -> None:
    """Which evaluator raised what is the whole value of the list, so the pairing
    has to survive being written and read back, not just being built."""
    rec = EvalRecorder()
    rec.add_round(
        "t",
        make_round(
            attempts=[
                make_attempt(
                    "c",
                    errors=[
                        AttemptErrorRecord(message="judge API down", evaluator="Judge"),
                        AttemptErrorRecord(message="no rubric", evaluator="Rubric"),
                    ],
                )
            ]
        ),
    )

    restored = parse_run_json(dump_run_json(rec.to_run_record()))
    [attempt] = restored.tests["t"].cases["c"].attempts
    assert [(e.evaluator, e.message) for e in attempt.errors] == [
        ("Judge", "judge API down"),
        ("Rubric", "no rubric"),
    ]


def test_repeat_with_one_crashed_attempt_counts_it() -> None:
    """A `repeat=3` case where the task raises once. The report comes from a
    real `Dataset.evaluate`, because a hand-built one does not show the shape
    a crash produces. The two surviving attempts must not carry the case to a
    clean verdict."""
    calls = itertools.count(1)

    def task(text: str) -> str:
        if next(calls) == 1:
            raise RuntimeError("boom")
        return text

    dataset = Dataset[str, str, None](
        name="ds", cases=[Case(name="c", inputs="hi", expected_output="hi")]
    )
    # Serial, so the crash is deterministically the first call.
    report = asyncio.run(
        dataset.evaluate(task, repeat=3, progress=False, max_concurrency=1)
    )

    rec = EvalRecorder()
    rec.add_round("t", translate_report(report))
    built = rec.to_run_record().tests["t"]
    recorded = built.cases["c"]
    assert recorded.clean_attempts == 2
    assert recorded.passed_attempts == 2
    assert recorded.errored_attempts == 1
    assert recorded.outcome == "errored"
    # Attempts come back in report order, so the crash trails the two completed
    # ones even though it ran first. Which attempt crashed is in the pytest
    # failure, not the run.
    assert [a.outcome for a in recorded.attempts] == ["passed", "passed", "errored"]


def test_records_task_duration() -> None:
    rec = EvalRecorder()
    rec.add_round("t", make_round(task_duration=2.5))
    test = rec.to_run_record().tests["t"]
    assert test.cases["test_case"].attempts[0].task_duration == 2.5


# rerun rounds: appended attempts


def test_rerun_rounds_append_attempts() -> None:
    rec = EvalRecorder()
    # Round 1: the case fails. Round 2 (the flake rerun loop's): it passes.
    rec.add_round("t", make_round(assertions={"passed": False}))
    rec.add_round("t", make_round(assertions={"passed": True}), continuing=True)
    run = rec.to_run_record()

    test = run.tests["t"]
    case = test.cases["test_case"]
    assert case.clean_attempts == 2
    assert case.passed_attempts == 1
    assert case.outcome == "passed"  # a passing attempt settles the case
    # The first round's failure is kept, never replaced by the gated result.
    assert case.attempts[0].outcome == "failed"
    assert len(case.attempts) == 2


def test_a_crash_outranks_a_clean_pass_in_another_round() -> None:
    """A case that passed cleanly once and crashed in a later round is
    `errored`, not `passed`. A crash already wins over a failure, so it must
    win over a pass too. Otherwise the same evidence gives two verdicts."""
    rec = EvalRecorder()
    rec.add_round("t", make_round())
    rec.add_round("t", make_crash_round(), continuing=True)
    case = rec.to_run_record().tests["t"].cases["test_case"]
    assert [a.outcome for a in case.attempts] == ["passed", "errored"]
    assert case.outcome == "errored"
    # The clean pass is still counted, so nothing about it is lost.
    assert case.passed_attempts == 1
    assert case.clean_attempts == 1
    assert case.errored_attempts == 1


def test_the_knobs_are_recorded_on_meta() -> None:
    """The rollup differs per knob, so a reader needs the knob to recheck a
    stored verdict."""
    rec = EvalRecorder()
    rec.add_round(
        "t", make_repeat_round([True, True]), settings=MarkerSettings(repeats=2)
    )
    rec.add_round("u", make_round(), settings=MarkerSettings(flake_reruns=2))
    # Bars reach the recorder already resolved onto the results, so `score_bars`
    # records only what was asked for.
    rec.add_round(
        "v", make_round(), settings=MarkerSettings(score_bars={"quality": 0.9})
    )
    run = rec.to_run_record()
    meta_t, meta_u = run.tests["t"].marker, run.tests["u"].marker
    meta_v = run.tests["v"].marker
    assert meta_t is not None and (meta_t.repeats, meta_t.flake_reruns) == (2, 0)
    assert meta_u is not None and (meta_u.repeats, meta_u.flake_reruns) == (1, 2)
    assert meta_v is not None and meta_v.score_bars == {"quality": 0.9}


@pytest.mark.parametrize(
    ("attempts", "repeats", "expected"),
    [
        ([True, False], 1, "passed"),
        ([True, False], 2, "failed"),
        ([True, True], 2, "passed"),
    ],
    ids=["flake-settles", "repeat-demands", "repeat-all-pass"],
)
def test_case_outcome_can_be_recomputed_from_attempts_and_meta(
    attempts: list[bool], repeats: int, expected: CaseOutcome
) -> None:
    """The same attempts roll up differently per knob, so the stored run
    carries the knob on the marker and a reader can recheck the verdict."""
    rec = EvalRecorder()
    settings = MarkerSettings(repeats=repeats)
    rec.add_round(
        "t", make_round(assertions={"passed": attempts[0]}), settings=settings
    )
    rec.add_round(
        "t",
        make_round(assertions={"passed": attempts[1]}),
        continuing=True,
        settings=settings,
    )
    run = rec.to_run_record()
    case = run.tests["t"].cases["test_case"]
    assert case.outcome == expected
    assert (case.passed_attempts, case.clean_attempts) == (
        attempts.count(True),
        len(attempts),
    ), "a driven round appends an attempt rather than replacing the round before"
    settings = run.tests["t"].marker
    assert settings is not None and settings.repeats == repeats


def test_a_crashed_attempt_lands_beside_the_clean_one() -> None:
    """A task that raised is projected off `report.failures`, not off the cases,
    so it reaches the case's attempts by its own path."""
    rec = EvalRecorder()
    rec.add_round("t", make_round())
    rec.add_round("t", make_crash_round(), continuing=True)
    case = rec.to_run_record().tests["t"].cases["test_case"]
    assert [a.outcome for a in case.attempts] == ["passed", "errored"]


def test_reliability_target_and_eval_version_recorded_on_meta() -> None:
    rec = EvalRecorder()
    rec.add_round(
        "t",
        make_round(),
        settings=MarkerSettings(reliability_target=0.9, eval_version="v3"),
    )
    test = rec.to_run_record().tests["t"]
    assert test.marker is not None
    assert test.marker.reliability_target == 0.9
    assert test.marker.eval_version == "v3"


# one-eval guard: a test records one top-level Dataset.evaluate()


def test_second_evaluate_still_raises_after_a_rerun() -> None:
    """The one-eval guard must survive a rerun in between.

    The old guard compared report names, and the rerun's own recording reset
    the state it compared against. A second evaluate after a rerun then passed
    unnoticed, and mixed two evals under one test's identity.
    """
    rec = EvalRecorder()
    rec.add_round("t", make_round(assertions={"passed": False}))
    rec.add_round("t", make_round(assertions={"passed": True}), continuing=True)
    with pytest.raises(EvalDefinitionError, match="already recorded an eval"):
        rec.add_round("t", make_round())


def test_rerun_reports_merge_into_multi_attempt_cases() -> None:
    """The rerun loop records extra rounds with `continuing=True`. Any number
    of them must append attempts to the same case without tripping the one-eval
    guard, or reruns beyond the first would crash the test they retry."""
    rec = EvalRecorder()
    rec.add_round("t", make_round(assertions={"passed": False}))
    rec.add_round("t", make_round(assertions={"passed": False}), continuing=True)
    rec.add_round("t", make_round(assertions={"passed": True}), continuing=True)
    run = rec.to_run_record()
    test = run.tests["t"]
    case = test.cases["test_case"]
    assert case.clean_attempts == 3
    assert case.passed_attempts == 1
    assert case.attempts[0].outcome == "failed"
    assert case.outcome == "passed"  # the last round passed


# has_evaluated: whether the test recorded an eval


def test_has_evaluated_is_false_for_a_test_that_never_evaluated() -> None:
    rec = EvalRecorder()
    rec.set_test_outcome("t", "passed")
    assert rec.has_evaluated("t") is False
    assert rec.has_evaluated("never-seen") is False


def test_has_evaluated_is_true_once_the_test_recorded_an_eval() -> None:
    rec = EvalRecorder()
    rec.add_round("t", make_round())
    assert rec.has_evaluated("t") is True


# set_test_outcome


def test_set_test_outcome_round_trips_via_eval_run() -> None:
    rec = EvalRecorder()
    rec.set_test_outcome("test_foo", "passed")
    rec.set_test_outcome("test_bar", "failed")
    rec.add_round("test_foo", make_round())

    run = rec.to_run_record()
    assert run.tests["test_foo"].outcome == "passed"
    assert run.tests["test_bar"].outcome == "failed"


def test_set_test_outcome_overwrites() -> None:
    """A later call wins. pytest's makereport hook fires several times per
    test, and only the final `call`-phase verdict counts."""
    rec = EvalRecorder()
    rec.set_test_outcome("t", "passed")
    rec.set_test_outcome("t", "failed")
    assert rec.to_run_record().tests["t"].outcome == "failed"


def test_an_eval_with_no_case_still_records_that_it_evaluated() -> None:
    """A test that evaluated an empty dataset and one that never evaluated both
    record no case. `marker` is what tells them apart, and readers that count evals
    depend on it."""
    rec = EvalRecorder()
    rec.add_round("t_empty", make_round(attempts=[]))
    rec.set_test_outcome("t_never", "skipped")
    run = rec.to_run_record()

    assert run.tests["t_empty"].cases == {}
    assert run.tests["t_empty"].marker is not None
    assert run.tests["t_never"].cases == {}
    assert run.tests["t_never"].marker is None


def test_outcome_without_rounds_builds_an_empty_recorded_test() -> None:
    """A test that errors in setup records an outcome but never an eval.

    Any setter creates the test's record, so the built run still carries a
    `RecordedTest` with `marker=None` and the test's verdict. `has_rounds` stays
    False, because nothing was recorded.
    """
    rec = EvalRecorder()
    rec.set_test_file("test_broken_setup", "tests/test_x.py")
    rec.set_test_outcome("test_broken_setup", "errored")
    assert rec.has_rounds is False

    run = rec.to_run_record()
    assert set(run.tests) == {"test_broken_setup"}
    test_result = run.tests["test_broken_setup"]
    assert test_result.outcome == "errored"
    assert test_result.marker is None
    # The module is recorded even with no eval, so a reader can still place the
    # failing test under its module.
    assert test_result.test_file == "tests/test_x.py"


def test_records_test_file_for_reported_test() -> None:
    rec = EvalRecorder()
    rec.set_test_file("test_x", "tests/evals/test_x.py")
    rec.add_round("test_x", make_round())
    assert rec.to_run_record().tests["test_x"].test_file == "tests/evals/test_x.py"


def test_a_task_failure_is_kept_beside_an_evaluator_that_also_failed() -> None:
    """A task that raised and an evaluator that raised are different findings,
    and a reader needs both: the task raising is usually why the evaluator had
    nothing to judge. Storing one would hide that."""
    rec = EvalRecorder()
    rec.add_round(
        "t",
        make_round(
            attempts=[
                make_attempt(
                    "c",
                    errors=[
                        AttemptErrorRecord(message="judge down", evaluator="Judge"),
                        AttemptErrorRecord(message="RuntimeError: boom"),
                    ],
                )
            ]
        ),
    )

    [attempt] = rec.to_run_record().tests["t"].cases["c"].attempts
    assert [(e.evaluator, e.message) for e in attempt.errors] == [
        ("Judge", "judge down"),
        (None, "RuntimeError: boom"),
    ], "both are kept, in the order the runner reported them"


def test_runner_details_reach_the_stored_run() -> None:
    """`details` is the escape hatch a translator puts runner-specific data in,
    at both levels. It is proven at the translators and declared on the stored
    model, but the recorder is what has to carry it between the two."""
    rec = EvalRecorder()
    rec.add_round(
        "t",
        make_round(
            attempts=[make_attempt("c", details={"trace": "abc"})],
            details={"log_dir": "/tmp/run"},
        ),
    )

    test = rec.to_run_record().tests["t"]
    assert test.details == {"log_dir": "/tmp/run"}
    assert test.cases["c"].attempts[0].details == {"trace": "abc"}


def test_an_xfailed_test_is_not_counted_as_a_failure() -> None:
    """`has_failures` decides whether the session prints the dashboard hint. An
    expected failure is not news, so a suite full of them must not read as a
    failing run."""
    rec = EvalRecorder()
    rec.set_test_outcome("t", "xfailed")
    assert rec.has_failures is False
    rec.set_test_outcome("u", "failed")
    assert rec.has_failures is True


# --- large outputs ---


def test_an_output_over_the_limit_is_listed_with_its_test_and_case() -> None:
    rec = EvalRecorder(large_output_bytes=100)
    rec.add_round(
        "test_x.py::test_a",
        make_round(
            attempts=[
                make_attempt(case_id="small", output="x"),
                make_attempt(case_id="big", output="x" * 200),
            ]
        ),
    )
    [large] = rec.large_outputs
    assert (large.nodeid, large.case_id) == ("test_x.py::test_a", "big")
    assert large.size > 200, "the size is the stored JSON's, quotes included"


def test_the_stored_size_is_measured_not_the_python_object() -> None:
    """A model with an excluded field is measured as the run stores it, without
    that field."""

    class _Bulky(BaseModel):
        shown: str
        hidden: str = Field(exclude=True)

    rec = EvalRecorder(large_output_bytes=100)
    rec.add_round(
        "t",
        make_round(attempts=[make_attempt(output=_Bulky(shown="s", hidden="h" * 500))]),
    )
    assert rec.large_outputs == []
