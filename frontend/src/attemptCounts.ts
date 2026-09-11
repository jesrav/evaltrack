// How a case's attempts are tallied when one of them errored.

import type { CaseRecord } from "./types";

/** One outcome and how many attempts reached it. */
export interface AttemptCount {
  n: number;
  label: string;
}

/** A case's attempts counted by outcome, or null when none errored.
 *
 *  An errored attempt reaches no verdict, so it is not one of the attempts the
 *  pass rate is taken over. "2/2 attempts passed" beside an errored verdict
 *  therefore looks like a contradiction, and these counts say what happened
 *  instead. Null keeps the pass-rate wording for every other case. Outcomes
 *  nothing reached are left out.
 *
 *  Pure and exported, so tests check the tally rather than a rendered row. */
export function countAttemptsByOutcome(
  c: Pick<
    CaseRecord,
    "passed_attempts" | "clean_attempts" | "errored_attempts"
  >,
): AttemptCount[] | null {
  if (c.errored_attempts <= 0) return null;
  return [
    { n: c.passed_attempts, label: "passed" },
    { n: c.clean_attempts - c.passed_attempts, label: "failed" },
    { n: c.errored_attempts, label: "errored" },
  ].filter((x) => x.n > 0);
}

/** The same tally as one line, for a tooltip. Null when nothing errored. */
export function formatAttemptCounts(
  c: Pick<
    CaseRecord,
    "passed_attempts" | "clean_attempts" | "errored_attempts"
  >,
): string | null {
  const counts = countAttemptsByOutcome(c);
  if (counts === null) return null;
  return counts.map((x) => `${x.n} ${x.label}`).join(" · ");
}
