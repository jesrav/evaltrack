// A run's summary counts say how many tests were recorded and how many failed
// or errored. Skips and xfails sit inside the total, so the two counts do not
// give a pass count. The chip claims a failure count, or that no test failed,
// never "all passing".

import { formatPlural } from "./format";

/** What the chip renders: the mark, its hover title, and which state it is. */
export interface OutcomeChip {
  failing: boolean;
  label: string;
  title: string;
}

/** Chip content for a run's recorded counts, or null for a run that recorded
 *  no tests and so has no outcome to show. */
export function buildRunOutcomeChip(
  testsTotal: number,
  testsFailed: number,
): OutcomeChip | null {
  if (testsTotal === 0) return null;
  if (testsFailed > 0) {
    return {
      failing: true,
      label: `${testsFailed}✗`,
      title: `${testsFailed} of ${formatPlural(testsTotal, "recorded test")} failing`,
    };
  }
  return {
    failing: false,
    label: "✓",
    title: `no failures in ${formatPlural(testsTotal, "recorded test")}`,
  };
}
