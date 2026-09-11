import { describe, it, expect } from "vitest";

import {
  scoreClearsBar,
  findDecidingAttempt,
  findDecidingAttemptIndex,
  evaluatorPasses,
  getEvaluatorTone,
  recoveredOnARerun,
} from "./checks";
import { buildCaseResult, buildAttempt, buildOutcome } from "./test-support";

describe("findDecidingAttemptIndex", () => {
  // The tick strip and the attempt selector number the attempts straight
  // through, earliest round first.
  it("counts from the earliest attempt", () => {
    const c = buildCaseResult({
      outcome: "passed",
      attempts: [buildAttempt(false), buildAttempt(false), buildAttempt(true)],
    });
    expect(findDecidingAttemptIndex(c)).toBe(2);
  });
});

describe("scoreClearsBar", () => {
  it("passes only when a bar exists and the value reaches it", () => {
    expect(scoreClearsBar(0.9, 0.8)).toBe(true);
    expect(scoreClearsBar(0.8, 0.8)).toBe(true);
    expect(scoreClearsBar(0.7, 0.8)).toBe(false);
    expect(scoreClearsBar(1, undefined)).toBe(false);
  });
});

describe("evaluatorPasses", () => {
  // A score with no bar is recorded history, not a verdict, so it must not count
  // as a failure the way a barless assertion does.
  it("passes a score with no bar and fails an assertion that is not true", () => {
    expect(evaluatorPasses(buildOutcome(0.1))).toBe(true);
    expect(evaluatorPasses(buildOutcome(0.7, 0.8))).toBe(false);
    expect(evaluatorPasses(buildOutcome(false))).toBe(false);
    expect(evaluatorPasses(buildOutcome(true))).toBe(true);
  });
});

describe("getEvaluatorTone", () => {
  it("keeps a barless score neutral and colors everything else", () => {
    expect(getEvaluatorTone(buildOutcome(0.9))).toBe("neutral");
    expect(getEvaluatorTone(buildOutcome(0.7, 0.8))).toBe("fail");
    expect(getEvaluatorTone(buildOutcome(true))).toBe("pass");
  });
});

describe("recoveredOnARerun", () => {
  const caseOf = (
    outcome: "passed" | "failed" | "errored",
    ...tries: string[]
  ) =>
    buildCaseResult({
      outcome,
      attempts: tries.map((t) =>
        t === "e"
          ? buildAttempt(false, { outcome: "errored" })
          : buildAttempt(t === "p"),
      ),
    });

  it("marks a case that failed first and passed after a rerun", () => {
    expect(recoveredOnARerun(caseOf("passed", "f", "p"))).toBe(true);
  });

  it("leaves a case that passed outright alone", () => {
    expect(recoveredOnARerun(caseOf("passed", "p"))).toBe(false);
  });

  // An errored attempt reached no verdict, so the first clean one is the first
  // try. Reading attempts[0] instead would take a crash for a failure.
  it("skips an errored attempt to find the first try", () => {
    expect(recoveredOnARerun(caseOf("passed", "e", "p"))).toBe(false);
    expect(recoveredOnARerun(caseOf("passed", "e", "f", "p"))).toBe(true);
  });

  it("marks nothing on a case that never passed", () => {
    expect(recoveredOnARerun(caseOf("failed", "f", "f"))).toBe(false);
    expect(recoveredOnARerun(caseOf("errored", "e"))).toBe(false);
  });
});

describe("findDecidingAttempt", () => {
  it("uses the last failing attempt for a failed case", () => {
    const c = buildCaseResult({
      outcome: "failed",
      attempts: [buildAttempt(false), buildAttempt(false)],
    });
    expect(findDecidingAttemptIndex(c)).toBe(1);
  });

  it("skips a later pass for a failed case (repeats)", () => {
    // Every attempt runs under repeats and one failure fails the case, so a red
    // case can end on a pass. Showing that pass as the evidence contradicts the
    // outcome beside it.
    const c = buildCaseResult({
      outcome: "failed",
      attempts: [buildAttempt(true), buildAttempt(false), buildAttempt(true)],
    });
    expect(findDecidingAttempt(c)?.outcome).toBe("failed");
    expect(findDecidingAttemptIndex(c)).toBe(1);
  });

  it("uses the first passing attempt for a passed case (rerun recovery)", () => {
    const c = buildCaseResult({
      outcome: "passed",
      attempts: [buildAttempt(false), buildAttempt(true)],
    });
    expect(findDecidingAttempt(c)?.outcome === "passed").toBe(true);
    expect(findDecidingAttemptIndex(c)).toBe(1);
  });

  it("returns null when a case recorded no attempts", () => {
    expect(findDecidingAttempt(buildCaseResult({ attempts: [] }))).toBe(null);
  });
});
