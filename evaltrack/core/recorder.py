"""Records each test's rounds during a pytest session and produces a
`RunRecord`. The run's id is a ULID assigned at construction."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ulid import ULID

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound
from evaltrack.core.projection import build_case_records
from evaltrack.core.run_context import RunContext
from evaltrack.core.run_record import (
    FAILING_OUTCOMES,
    RUN_SCHEMA_VERSION,
    MarkerSettings,
    RecordedBy,
    RecordedTest,
    RunRecord,
    TestOutcome,
    dump_output_json,
)
from evaltrack.core.user_values import RawResult

LARGE_OUTPUT_BYTES: int = 256 * 1024
"""The stored size above which an attempt's output is reported as large."""


@dataclass(frozen=True)
class LargeOutput:
    """One attempt whose stored output is larger than the recorder's limit.

    Attributes:
        nodeid: The test that recorded it.
        case_id: The case the attempt belongs to.
        size: The output's stored size in bytes.
    """

    nodeid: str
    case_id: str
    size: int


@dataclass
class _TestAccumulator:
    """What the recorder accumulates for one test. A marked test that was
    collected but never evaluated, because it skipped or died in setup, has an
    entry here with no rounds."""

    rounds: list[EvalRound] = field(default_factory=list)
    raw_results: list[RawResult] = field(default_factory=list)
    settings: MarkerSettings | None = None
    outcome: TestOutcome | None = None
    docstring: str | None = None
    test_file: str | None = None


def _second_eval_error(nodeid: str) -> EvalDefinitionError:
    """The refusal for a second top-level eval, whether the test body evaluated
    twice or the whole test ran again."""
    return EvalDefinitionError(
        f"test {nodeid!r} already recorded an eval, and a test records one: "
        "the test's verdict is that eval's. Put a second eval in a test of its "
        "own. For repetition, use the marker's repeats= or flake_reruns=, "
        "which add rounds to the eval already recorded. If a plugin that "
        "re-runs failed tests ran this test again, keep that plugin off your "
        "eval tests. See "
        "https://github.com/jesrav/evaltrack/blob/main/docs/flakiness.md"
        "#rerun-on-failure-flake_reruns"
    )


class EvalRecorder:
    """Records the rounds of every eval in one session.

    Args:
        context: Run metadata. Only `to_run_record` reads it, so it can be
            assigned later.
        run_id: Defaults to a fresh ULID.
        created_at: Defaults to the current UTC time.
        keep_raw_results: Keep each round's runner-native result for export.
        large_output_bytes: The stored size above which an attempt's output is
            listed in `large_outputs`.
    """

    def __init__(
        self,
        context: RunContext | None = None,
        *,
        run_id: str | None = None,
        created_at: datetime | None = None,
        keep_raw_results: bool = False,
        large_output_bytes: int = LARGE_OUTPUT_BYTES,
    ) -> None:
        self.id: str = run_id or str(ULID())
        self.created_at: datetime = created_at or datetime.now(UTC)
        self.context: RunContext = context or RunContext()
        self.keep_raw_results: bool = keep_raw_results
        self.large_output_bytes: int = large_output_bytes
        self.tests: dict[str, _TestAccumulator] = {}
        self.large_outputs: list[LargeOutput] = []

    def _get_recorded_test(self, nodeid: str) -> _TestAccumulator:
        return self.tests.setdefault(nodeid, _TestAccumulator())

    @property
    def has_rounds(self) -> bool:
        return any(rec.rounds for rec in self.tests.values())

    @property
    def has_failures(self) -> bool:
        return any(rec.outcome in FAILING_OUTCOMES for rec in self.tests.values())

    def has_evaluated(self, nodeid: str) -> bool:
        """Whether the test recorded a top-level eval."""
        rec = self.tests.get(nodeid)
        return rec is not None and bool(rec.rounds)

    def add_round(
        self,
        nodeid: str,
        eval_round: EvalRound,
        *,
        raw_result: RawResult = None,
        continuing: bool = False,
        settings: MarkerSettings | None = None,
    ) -> None:
        """Record one round for a test. The first round's `settings` is kept.

        Args:
            continuing: True for every round after the test's first, whichever
                marker setting asked for it.

        Raises:
            EvalDefinitionError: when the test has already started an eval and this
                is not another round of it. A test records one eval.
        """
        rec = self._get_recorded_test(nodeid)
        if not continuing and rec.rounds:
            raise _second_eval_error(nodeid)
        rec.rounds.append(eval_round)
        if self.keep_raw_results:
            rec.raw_results.append(raw_result)
        if rec.settings is None:
            rec.settings = settings or MarkerSettings()
        self._note_large_outputs(nodeid, eval_round)

    def _note_large_outputs(self, nodeid: str, eval_round: EvalRound) -> None:
        """Measured per attempt, so that the report can name the case."""
        for attempt in eval_round.attempts:
            if attempt.output is None:
                continue
            size = len(dump_output_json(attempt))
            if size > self.large_output_bytes:
                self.large_outputs.append(LargeOutput(nodeid, attempt.case_id, size))

    def set_test_outcome(self, nodeid: str, outcome: TestOutcome) -> None:
        """Record a test's pytest outcome, the authoritative pass/fail for the test."""
        self._get_recorded_test(nodeid).outcome = outcome

    def get_test_outcome(self, nodeid: str) -> TestOutcome | None:
        """The outcome recorded so far, or None while the test has none."""
        rec = self.tests.get(nodeid)
        return rec.outcome if rec is not None else None

    def set_test_file(self, nodeid: str, test_file: str | None) -> None:
        self._get_recorded_test(nodeid).test_file = test_file

    def set_test_docstring(self, nodeid: str, docstring: str | None) -> None:
        """Record the test's docstring. An empty one is ignored."""
        if docstring:
            self._get_recorded_test(nodeid).docstring = docstring

    def to_run_record(self) -> RunRecord:
        """Build the recorded state into a `RunRecord`, with each test's rounds merged
        into multi-attempt cases."""
        tests = {
            nodeid: self._build_recorded_test(rec) for nodeid, rec in self.tests.items()
        }
        return RunRecord(
            run_schema_version=RUN_SCHEMA_VERSION,
            id=self.id,
            created_at=self.created_at,
            recorded_by=RecordedBy.from_installed(),
            commit=self.context.commit,
            worktree_dirty=self.context.worktree_dirty,
            labels=self.context.labels,
            tests=tests,
        )

    def _build_recorded_test(self, rec: _TestAccumulator) -> RecordedTest:
        """Details come from the first round. Round-level failures come from every
        round, since a rerun is another attempt at the same eval and each round
        failed for its own reason."""
        # The fallback only satisfies the type checker.
        settings = (rec.settings or MarkerSettings()) if rec.rounds else None
        first = rec.rounds[0] if rec.rounds else None
        return RecordedTest(
            cases=build_case_records(rec.rounds, repeats=settings.repeats)
            if settings
            else {},
            errors=[error for round_ in rec.rounds for error in round_.errors],
            marker=settings,
            runner=first.runner if first else None,
            raw_results=rec.raw_results,
            outcome=rec.outcome,
            docstring=rec.docstring,
            test_file=rec.test_file,
            details=first.details if first else {},
        )
