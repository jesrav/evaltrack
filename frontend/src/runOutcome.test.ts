import { describe, it, expect } from "vitest";

import { buildRunOutcomeChip } from "./runOutcome";

describe("buildRunOutcomeChip", () => {
  it("gives no chip for a run that recorded no tests", () => {
    // Such a run has no outcome, so a green tick is wrong.
    expect(buildRunOutcomeChip(0, 0)).toBeNull();
  });

  it("counts failures when there are any", () => {
    expect(buildRunOutcomeChip(3, 2)).toEqual({
      failing: true,
      label: "2✗",
      title: "2 of 3 recorded tests failing",
    });
  });

  it("keeps the failing title singular for a single recorded test", () => {
    expect(buildRunOutcomeChip(1, 1)?.title).toBe(
      "1 of 1 recorded test failing",
    );
  });

  it("claims only the absence of failures when nothing failed", () => {
    // The total includes skips and xfails, so a run with 1 passed + 2 skipped
    // must not show as "3 of 3 passing".
    expect(buildRunOutcomeChip(3, 0)).toEqual({
      failing: false,
      label: "✓",
      title: "no failures in 3 recorded tests",
    });
  });

  it("keeps the no-failure title singular for a single recorded test", () => {
    expect(buildRunOutcomeChip(1, 0)?.title).toBe(
      "no failures in 1 recorded test",
    );
  });
});
