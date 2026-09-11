import { describe, it, expect } from "vitest";

import { countAttemptsByOutcome, formatAttemptCounts } from "./attemptCounts";
import { buildAttempt, buildCaseResult } from "./test-support";

// An errored attempt is left out of the pass rate. Read together, the verdict
// and the rate then claim opposite things ("errored · 2/2 attempts passed"),
// which is the one place the case summary can mislead.
describe("attempt counts", () => {
  it("counts every outcome once an attempt errored", () => {
    const c = buildCaseResult({
      outcome: "errored",
      attempts: [
        buildAttempt(true),
        buildAttempt(true),
        buildAttempt(false, { outcome: "errored" }),
      ],
    });

    expect(formatAttemptCounts(c)).toBe("2 passed · 1 errored");
  });

  it("keeps failed apart from errored", () => {
    const c = buildCaseResult({
      outcome: "errored",
      attempts: [
        buildAttempt(true),
        buildAttempt(false),
        buildAttempt(false, { outcome: "errored" }),
      ],
    });

    expect(formatAttemptCounts(c)).toBe("1 passed · 1 failed · 1 errored");
  });

  it("leaves out an outcome nothing reached", () => {
    const c = buildCaseResult({
      outcome: "errored",
      attempts: [buildAttempt(false, { outcome: "errored" })],
    });

    expect(countAttemptsByOutcome(c)).toEqual([{ n: 1, label: "errored" }]);
  });

  it("declines the tally when nothing errored, so the pass rate stands", () => {
    const c = buildCaseResult({
      outcome: "failed",
      attempts: [buildAttempt(true), buildAttempt(false)],
    });

    expect(countAttemptsByOutcome(c)).toBeNull();
    expect(formatAttemptCounts(c)).toBeNull();
  });
});
