// Small pure helpers over a case. Whether a score clears its per-case bar, and
// which attempt decided the outcome.

import type {
  AttemptOutcome,
  AttemptRecord,
  CaseOutcome,
  CaseRecord,
  EvaluatorResult,
} from "./types";

/** The parts of a result that decide whether it clears the gate. */
type GatedResult = Pick<EvaluatorResult, "verdict">;

/** Doubles as the CSS class of the pill or tick that shows it. */
export const OUTCOME_LABEL: Record<AttemptOutcome | CaseOutcome, string> = {
  passed: "pass",
  failed: "fail",
  errored: "errored",
};

export const OUTCOME_MARK: Record<AttemptOutcome, string> = {
  passed: "\u2713",
  failed: "\u2717",
  errored: "\u26a0",
};

/** A score passes its bar iff a bar exists for it and the value reaches it. */
export function scoreClearsBar(
  value: number,
  bar: number | undefined,
): boolean {
  return bar !== undefined && value >= bar;
}

/** Whether one evaluator result counts as a pass, by the rule the run itself
 *  was gated on. A null verdict is a result nothing gated, which has nothing to
 *  fail: a label, or a score with no bar. */
export function evaluatorPasses(result: GatedResult): boolean {
  return result.verdict !== false;
}

/** Pill tone for one evaluator result. A result nothing gated carries no pass
 *  or fail information, so it stays neutral. */
export function getEvaluatorTone(result: GatedResult): string {
  if (result.verdict == null) return "neutral";
  return result.verdict ? "pass" : "fail";
}

/** Whether a rerun rescued this case: it passed, but its first attempt that
 *  did not error failed. A case whose every attempt errored never passed, so
 *  it is not one. */
export function recoveredOnARerun(c: CaseRecord): boolean {
  if (c.outcome !== "passed") return false;
  const firstClean = c.attempts.find((a) => a.outcome !== "errored");
  return firstClean !== undefined && firstClean.outcome !== "passed";
}

/** The "deciding" attempt, the one that determined the case's outcome, used as
 *  the representative attempt the row shows and the inspector defaults to.
 *
 *  - Passed case: the first passing attempt. Under reruns that is the recovery
 *    that ended the loop. With no repetition it is the only attempt.
 *  - Errored case: the first errored attempt, the crash that stopped the case
 *    reaching a verdict.
 *  - Failed case: the last failing attempt. Under repeats every attempt runs
 *    and one failure fails the case, so a failed case can end on a pass, and
 *    showing that pass contradicts the outcome beside it. An exhausted
 *    rerun ends on a fail, so this is still its final attempt.
 */
export function findDecidingAttempt(c: CaseRecord): AttemptRecord | null {
  const { attempts, outcome } = c;
  if (outcome === "passed") {
    return (
      attempts.find((a) => a.outcome === "passed") ?? attempts.at(-1) ?? null
    );
  }
  if (outcome === "errored") {
    return (
      attempts.find((a) => a.outcome === "errored") ?? attempts.at(-1) ?? null
    );
  }
  return (
    [...attempts].reverse().find((a) => a.outcome === "failed") ??
    attempts.at(-1) ??
    null
  );
}

/** Index of `findDecidingAttempt` within `c.attempts` (0 when none). */
export function findDecidingAttemptIndex(c: CaseRecord): number {
  const d = findDecidingAttempt(c);
  const i = d ? c.attempts.indexOf(d) : 0;
  return i < 0 ? 0 : i;
}
