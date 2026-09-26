// Tiny factories for building RunRecord / CaseRecord shapes in unit tests. Kept
// in src/ (not a separate test dir) so it's typechecked with everything else.

import type {
  AttemptRecord,
  CaseRecord,
  RunRecord,
  EvaluatorResult,
  ReliabilityPoint,
  ScoreHistoryPoint,
  TestOutcome,
  RecordedTest,
} from "./types";

export function buildOutcome(
  value: number | boolean,
  bar?: number,
): EvaluatorResult {
  return {
    value,
    bar: bar ?? null,
    // Derived the way the model derives it, so no fixture carries a verdict no
    // stored run could.
    verdict:
      typeof value === "boolean"
        ? value
        : typeof value === "number" && bar !== undefined
          ? value >= bar
          : null,
    reason: null,
    evaluator: { name: "e", arguments: null },
  };
}

/** `passed` is the common case, so it stays a boolean here. Pass
 *  `{ outcome: "errored" }` for a crash. */
export function buildAttempt(
  passed: boolean,
  opts: Partial<AttemptRecord> = {},
): AttemptRecord {
  return {
    outcome: passed ? "passed" : "failed",
    output: undefined,
    results: {},
    task_duration: 0,
    ...opts,
  };
}

/** A CaseRecord with sensible defaults. Pass `outcome` or `attempts` to shape
 *  it. `passed_attempts` and `clean_attempts` are derived from the attempts
 *  unless overridden. */
export function buildCaseResult(
  opts: Partial<CaseRecord> & { attempts?: AttemptRecord[] } = {},
): CaseRecord {
  const { attempts: given, ...rest } = opts;
  const outcome = rest.outcome ?? "passed";
  const attempts = given ?? [buildAttempt(outcome === "passed")];
  return {
    inputs: undefined,
    expected_output: undefined,
    metadata: undefined,
    attempts,
    passed_attempts: attempts.filter((a) => a.outcome === "passed").length,
    clean_attempts: attempts.filter((a) => a.outcome !== "errored").length,
    errored_attempts: attempts.filter((a) => a.outcome === "errored").length,
    outcome,
    ...rest,
  };
}

/** One run's reliability samples. `attempts` holds the pass or fail of each
 *  clean attempt. `overlay` marks the run being viewed, which is never
 *  pooled. */
export function buildReliabilityPoint(
  runId: string,
  attempts: boolean[],
  opts: Partial<ReliabilityPoint> = {},
): ReliabilityPoint {
  return {
    run_id: runId,
    created_at: "2026-01-01T00:00:00Z",
    commit: null,
    passed_attempts: attempts.filter(Boolean).length,
    clean_attempts: attempts.length,
    attempts,
    off_mainline: false,
    ...opts,
  };
}

/** One point of a case's score history. `value` doubles as the spread bounds,
 *  which is the single-attempt case. Pass `low`/`high` for a run that scored
 *  the case more than once. */
export function buildScorePoint(
  value: number,
  opts: Partial<ScoreHistoryPoint> = {},
): ScoreHistoryPoint {
  return {
    run_id: "r",
    created_at: "2026-01-01T00:00:00Z",
    commit: null,
    pr: null,
    title: null,
    value,
    low: value,
    high: value,
    attempts: 1,
    off_mainline: false,
    ...opts,
  };
}

export interface TestSpec {
  outcome?: TestOutcome | null;
  module?: string | null;
  // The test's cases. Omit the key for a test that never evaluated.
  cases?: Record<string, CaseRecord>;
}

/** Build an RunRecord from a compact {test: {cases: {...}}} spec. */
export function buildRun(
  id: string,
  tests: Record<string, TestSpec>,
): RunRecord {
  const built: Record<string, RecordedTest> = {};
  for (const [nodeid, t] of Object.entries(tests)) {
    const evaluated = "cases" in t;
    built[nodeid] = {
      cases: t.cases ?? {},
      marker: evaluated
        ? {
            score_bars: {},
            repeats: 1,
            flake_reruns: 0,
            reliability_target: null,
            eval_version: null,
          }
        : null,
      runner: { name: "pydantic-evals", version: "0.0.0" },
      outcome: t.outcome ?? null,
      docstring: null,
      test_file: t.module ?? null,
    };
  }
  return {
    id,
    created_at: "2026-01-01T00:00:00Z",
    recorded_by: { evaltrack: "0.0.0" },
    commit: null,
    worktree_dirty: null,
    labels: {},
    tests: built,
  };
}
