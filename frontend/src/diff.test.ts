import { describe, it, expect } from "vitest";

import {
  diffRuns,
  hasDiffChanges,
  latencyChanged,
  summariseDiffModule,
  summariseTestDiff,
} from "./diff";
import {
  buildCaseResult,
  buildOutcome,
  buildAttempt,
  buildRun,
} from "./test-support";
import type { AttemptRecord, CaseOutcome } from "./types";

// A one-test, one-case diff where each side's deciding attempt is given.
function attemptDiff(a: AttemptRecord, b: AttemptRecord) {
  const ra = buildRun("a", {
    t: { cases: { c1: buildCaseResult({ attempts: [a] }) } },
  });
  const rb = buildRun("b", {
    t: { cases: { c1: buildCaseResult({ attempts: [b] }) } },
  });
  return diffRuns(ra, rb).tests[0]!.cases[0]!;
}

// One test ("t") with a single case whose outcome differs per side.
function oneCaseDiff(aVerdict: CaseOutcome, bVerdict: CaseOutcome) {
  const a = buildRun("a", {
    t: { cases: { c1: buildCaseResult({ outcome: aVerdict }) } },
  });
  const b = buildRun("b", {
    t: { cases: { c1: buildCaseResult({ outcome: bVerdict }) } },
  });
  return diffRuns(a, b).tests[0]!;
}

describe("diffRuns case verdicts", () => {
  it("counts a pass -> fail flip as newly failing and regressed", () => {
    const test = oneCaseDiff("passed", "failed");
    expect(test.newlyFailing).toBe(1);
    expect(test.newlyPassing).toBe(0);
    expect(test.verdictChange).toBe("regressed");
  });

  it("counts a fail -> pass flip as newly passing and recovered", () => {
    const test = oneCaseDiff("failed", "passed");
    expect(test.newlyPassing).toBe(1);
    expect(test.verdictChange).toBe("recovered");
  });

  it("counts a case passing on both sides as still passing", () => {
    const test = oneCaseDiff("passed", "passed");
    expect(test.stillPassing).toBe(1);
    expect(test.stillFailing).toBe(0);
    expect(test.verdictChange).toBe("unchanged");
  });

  it("counts a case failing on both sides as still failing, not unchanged", () => {
    // The outcome didn't flip, but a run that is red on both sides must not be
    // summarised as clean.
    const test = oneCaseDiff("failed", "failed");
    expect(test.stillFailing).toBe(1);
    expect(test.stillPassing).toBe(0);
    expect(test.verdictChange).toBe("unchanged");
  });

  it("counts a dropped score even when the outcome held", () => {
    // A passing case whose score fell must not be summarised as clean just
    // because nothing flipped.
    const withScore = (value: number, caseOutcome: CaseOutcome = "passed") =>
      buildCaseResult({
        outcome: caseOutcome,
        attempts: [
          buildAttempt(caseOutcome === "passed", {
            results: { quality: buildOutcome(value) },
          }),
        ],
      });
    const a = buildRun("a", { t: { cases: { c1: withScore(0.95) } } });
    const b = buildRun("b", { t: { cases: { c1: withScore(0.82) } } });
    const test = diffRuns(a, b).tests[0]!;
    expect(test.scoresRegressed).toBe(1);
    expect(test.newlyFailing).toBe(0);
    expect(test.stillPassing).toBe(1);
  });

  it("does not count a score move on a case whose verdict flipped", () => {
    // The flip already counts the case. Reporting it under "with a lower
    // score" too would sit a regression next to its own "newly passing".
    const withScore = (value: number, caseOutcome: CaseOutcome) =>
      buildCaseResult({
        outcome: caseOutcome,
        attempts: [
          buildAttempt(caseOutcome === "passed", {
            results: { stability: buildOutcome(value) },
          }),
        ],
      });
    const a = buildRun("a", {
      t: {
        cases: {
          up: withScore(0.8, "failed"),
          down: withScore(0.5, "passed"),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          up: withScore(0.7, "passed"),
          down: withScore(0.9, "failed"),
        },
      },
    });
    const test = diffRuns(a, b).tests[0]!;
    expect(test.newlyPassing).toBe(1);
    expect(test.newlyFailing).toBe(1);
    expect(test.scoresRegressed).toBe(0);
    expect(test.scoresImproved).toBe(0);
  });

  it("does not count a risen score as a regression", () => {
    const withScore = (value: number) =>
      buildCaseResult({
        attempts: [
          buildAttempt(true, { results: { quality: buildOutcome(value) } }),
        ],
      });
    const a = buildRun("a", { t: { cases: { c1: withScore(0.82) } } });
    const b = buildRun("b", { t: { cases: { c1: withScore(0.95) } } });
    const test = diffRuns(a, b).tests[0]!;
    expect(test.scoresRegressed).toBe(0);
    expect(test.scoresImproved).toBe(1);
  });

  it("ignores a score move under the noise floor", () => {
    // A sub-threshold wobble must not register, or the mainline banner reports
    // a score change on nearly every run.
    const withScore = (value: number) =>
      buildCaseResult({
        attempts: [
          buildAttempt(true, { results: { quality: buildOutcome(value) } }),
        ],
      });
    const a = buildRun("a", { t: { cases: { c1: withScore(0.9) } } });
    const b = buildRun("b", { t: { cases: { c1: withScore(0.89) } } });
    const test = diffRuns(a, b).tests[0]!;
    expect(test.scoresRegressed).toBe(0);
    expect(test.scoresImproved).toBe(0);
  });

  it("treats a mixed test as regressed (regression takes precedence)", () => {
    const a = buildRun("a", {
      t: {
        cases: {
          up: buildCaseResult({ outcome: "failed" }),
          down: buildCaseResult({ outcome: "passed" }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          up: buildCaseResult({ outcome: "passed" }),
          down: buildCaseResult({ outcome: "failed" }),
        },
      },
    });

    const test = diffRuns(a, b).tests[0]!;
    expect(test.newlyFailing).toBe(1);
    expect(test.newlyPassing).toBe(1);
    expect(test.verdictChange).toBe("regressed");
  });
});

describe("diffRuns presence", () => {
  it("attributes a case present on only one side to that side", () => {
    const a = buildRun("a", {
      t: { cases: { only_a: buildCaseResult() } },
    });
    const b = buildRun("b", {
      t: { cases: { only_b: buildCaseResult() } },
    });

    const test = diffRuns(a, b).tests[0]!;
    expect(test.onlyInA).toBe(1);
    expect(test.onlyInB).toBe(1);
    expect(test.newlyFailing).toBe(0);
  });

  it("lists tests present on only one side", () => {
    const a = buildRun("a", { onlyA: { cases: {} }, shared: { cases: {} } });
    const b = buildRun("b", { shared: { cases: {} }, onlyB: { cases: {} } });

    const d = diffRuns(a, b);
    expect(d.testsOnlyInA).toEqual(["onlyA"]);
    expect(d.testsOnlyInB).toEqual(["onlyB"]);
    expect(d.tests.map((t) => t.nodeid)).toEqual(["shared"]);
  });

  it("pairs a test that passed in A and crashed before evaluating in B, as an outcome flip", () => {
    // The crashed side recorded no cases and no meta. Listing it one-sided
    // hid the flip behind "No verdict changes".
    const a = buildRun("a", {
      t: { outcome: "passed", cases: { c: buildCaseResult() } },
    });
    const b = buildRun("b", { t: { outcome: "errored" } });

    const d = diffRuns(a, b);
    expect(d.tests.map((t) => t.nodeid)).toEqual(["t"]);
    expect(d.tests[0]!.outcomeChanged).toBe(true);
    expect(d.totals.outcomeFlips).toBe(1);
    expect(d.testsOnlyInA).toEqual([]);
    expect(d.testsOnlyInB).toEqual([]);
  });

  it("pairs a test that evaluated on B's side only", () => {
    const a = buildRun("a", { t: { outcome: "errored" } });
    const b = buildRun("b", { t: { outcome: "passed", cases: {} } });

    const d = diffRuns(a, b);
    expect(d.tests.map((t) => t.nodeid)).toEqual(["t"]);
    expect(d.totals.outcomeFlips).toBe(1);
    expect(d.testsOnlyInA).toEqual([]);
    expect(d.testsOnlyInB).toEqual([]);
  });

  it("lists a test that errored before evaluating and is absent from the other run", () => {
    // Such a test has no eval on either side, so a diff built on evaluated
    // tests alone loses it entirely.
    const a = buildRun("a", { t: { outcome: "errored" } });
    const b = buildRun("b", {});

    const d = diffRuns(a, b);
    expect(d.tests).toEqual([]);
    expect(d.testsOnlyInA).toEqual(["t"]);
    expect(d.testsOnlyInB).toEqual([]);
  });

  it("pairs a test present in both runs but evaluated in neither, with no case rows", () => {
    const a = buildRun("a", { t: { outcome: "errored" } });
    const b = buildRun("b", { t: { outcome: "skipped" } });

    const d = diffRuns(a, b);
    expect(d.tests).toHaveLength(1);
    expect(d.tests[0]!.cases).toEqual([]);
    expect(d.tests[0]!.outcomeChanged).toBe(true);
    expect(d.testsOnlyInA).toEqual([]);
    expect(d.testsOnlyInB).toEqual([]);
  });
});

describe("diffRuns outcome flips", () => {
  it("flags a changed pytest outcome even when case verdicts match", () => {
    const a = buildRun("a", {
      t: {
        outcome: "failed",
        cases: { c: buildCaseResult({ outcome: "failed" }) },
      },
    });
    const b = buildRun("b", {
      t: {
        outcome: "xfailed",
        cases: { c: buildCaseResult({ outcome: "failed" }) },
      },
    });

    const d = diffRuns(a, b);
    expect(d.tests[0]!.outcomeChanged).toBe(true);
    expect(d.totals.outcomeFlips).toBe(1);
  });
});

describe("diffRuns scores", () => {
  it("marks scoresChanged when a score value moved between the deciding attempts", () => {
    const a = buildRun("a", {
      t: {
        cases: {
          c: buildCaseResult({
            outcome: "passed",
            attempts: [
              buildAttempt(true, { results: { acc: buildOutcome(0.5) } }),
            ],
          }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          c: buildCaseResult({
            outcome: "passed",
            attempts: [
              buildAttempt(true, { results: { acc: buildOutcome(0.9) } }),
            ],
          }),
        },
      },
    });

    const row = diffRuns(a, b).tests[0]!.cases[0]!;
    expect(row.scoresChanged).toBe(true);
    expect(row.changed).toBe(true);
  });

  it("reads a failed side from the attempt that failed it, not a later pass", () => {
    // A repeats= case fails on any attempt, so the score that failed it can sit
    // behind an attempt that passed. Reading the last attempt hides the drop
    // and reports a higher score instead. The case fails on both sides so the
    // drop is the only change.
    const a = buildRun("a", {
      t: {
        cases: {
          c: buildCaseResult({
            outcome: "failed",
            attempts: [
              buildAttempt(false, { results: { acc: buildOutcome(0.9) } }),
            ],
          }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          c: buildCaseResult({
            outcome: "failed",
            attempts: [
              buildAttempt(false, { results: { acc: buildOutcome(0.1) } }),
              buildAttempt(true, { results: { acc: buildOutcome(0.95) } }),
            ],
          }),
        },
      },
    });

    const test = diffRuns(a, b).tests[0]!;
    expect(test.cases[0]!.scoresB.acc?.value).toBe(0.1);
    expect(test.scoresRegressed).toBe(1);
  });
});

describe("diffRuns totals", () => {
  it("aggregates per-test counts across tests", () => {
    const a = buildRun("a", {
      t1: { cases: { c: buildCaseResult({ outcome: "passed" }) } },
      t2: { cases: { c: buildCaseResult({ outcome: "failed" }) } },
    });
    const b = buildRun("b", {
      t1: { cases: { c: buildCaseResult({ outcome: "failed" }) } },
      t2: { cases: { c: buildCaseResult({ outcome: "passed" }) } },
    });

    const { totals } = diffRuns(a, b);
    expect(totals.newlyFailing).toBe(1);
    expect(totals.newlyPassing).toBe(1);
    expect(totals.testsRegressed).toBe(1);
    expect(totals.testsRecovered).toBe(1);
  });
});

describe("latencyChanged", () => {
  it("flags a change above both the relative and absolute floors", () => {
    expect(latencyChanged(1.0, 1.5)).toBe(true); // +50%, +0.5s
  });

  it("ignores a change below the absolute floor", () => {
    expect(latencyChanged(0.1, 0.15)).toBe(false); // +50% but only +0.05s
  });

  it("ignores a change below the relative floor", () => {
    expect(latencyChanged(10.0, 10.5)).toBe(false); // +0.5s but only +5%
  });

  it("returns false when either side is unrecorded", () => {
    expect(latencyChanged(null, 1.0)).toBe(false);
    expect(latencyChanged(1.0, null)).toBe(false);
  });
});

describe("diffRuns latency", () => {
  it("marks durationChanged for a meaningful latency move, neutral to `changed`", () => {
    const row = attemptDiff(
      buildAttempt(true, { task_duration: 1.0 }),
      buildAttempt(true, { task_duration: 1.5 }),
    );
    expect(row.durationA).toBe(1.0);
    expect(row.durationB).toBe(1.5);
    expect(row.durationChanged).toBe(true);
    // Latency alone is not a regression, so it does not flip `changed`.
    expect(row.changed).toBe(false);
  });

  it("does not flag sub-threshold latency jitter", () => {
    const row = attemptDiff(
      buildAttempt(true, { task_duration: 1.0 }),
      buildAttempt(true, { task_duration: 1.05 }),
    );
    expect(row.durationChanged).toBe(false);
  });
});

describe("diffRuns errored cases", () => {
  it("counts pass -> errored as errored, not newly failing", () => {
    // An infra crash is not a judged regression. The eval reached no verdict.
    const test = oneCaseDiff("passed", "errored");
    expect(test.newlyFailing).toBe(0);
    expect(test.errored).toBe(1);
    expect(test.verdictChange).toBe("unchanged");
  });

  it("counts errored -> pass as errored, not newly passing", () => {
    const test = oneCaseDiff("errored", "passed");
    expect(test.newlyPassing).toBe(0);
    expect(test.errored).toBe(1);
    expect(test.verdictChange).toBe("unchanged");
  });

  it("counts errored on both sides once, never as still failing", () => {
    const test = oneCaseDiff("errored", "errored");
    expect(test.errored).toBe(1);
    expect(test.stillFailing).toBe(0);
  });

  it("exposes the errored side as its own status, not as failing", () => {
    const test = oneCaseDiff("passed", "errored");
    expect(test.cases[0]!.a).toBe("passing");
    expect(test.cases[0]!.b).toBe("errored");
  });

  it("reports no latency for an errored side", () => {
    // A crashed attempt is stamped task_duration 0.0. Taken as measured, it
    // shows a false 1.2s -> 0ms latency win.
    const a = buildRun("a", {
      t: {
        cases: {
          c1: buildCaseResult({
            attempts: [buildAttempt(true, { task_duration: 1.2 })],
          }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          c1: buildCaseResult({
            outcome: "errored",
            attempts: [
              buildAttempt(false, { outcome: "errored", task_duration: 0 }),
            ],
          }),
        },
      },
    });
    const row = diffRuns(a, b).tests[0]!.cases[0]!;
    expect(row.durationB).toBeNull();
    expect(row.durationChanged).toBe(false);
  });

  it("skips score comparison against an errored side", () => {
    // Scores an attempt recorded before its crash are not a measurement.
    const a = buildRun("a", {
      t: {
        cases: {
          c1: buildCaseResult({
            attempts: [
              buildAttempt(true, { results: { acc: buildOutcome(0.9) } }),
            ],
          }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          c1: buildCaseResult({
            outcome: "errored",
            attempts: [
              buildAttempt(false, {
                outcome: "errored",
                results: { acc: buildOutcome(0.2) },
              }),
            ],
          }),
        },
      },
    });
    const test = diffRuns(a, b).tests[0]!;
    expect(test.scoresRegressed).toBe(0);
  });

  it("totals errored cases apart from the flip counts", () => {
    const a = buildRun("a", {
      t: {
        cases: {
          c1: buildCaseResult({ outcome: "passed" }),
          c2: buildCaseResult({ outcome: "passed" }),
        },
      },
    });
    const b = buildRun("b", {
      t: {
        cases: {
          c1: buildCaseResult({ outcome: "errored" }),
          c2: buildCaseResult({ outcome: "failed" }),
        },
      },
    });
    const totals = diffRuns(a, b).totals;
    expect(totals.casesErrored).toBe(1);
    expect(totals.newlyFailing).toBe(1);
    // c2's real pass -> fail flip still regresses the test.
    expect(totals.testsRegressed).toBe(1);
  });
});

// A module card opens on the same rule its badge uses. A badge that says
// "N cases with a lower score" over a collapsed card hides the drop it
// announces.
describe("hasDiffChanges", () => {
  const test = "tests/t.py::test_eval";
  const withScore = (value: number) =>
    buildCaseResult({
      attempts: [buildAttempt(true, { results: { q: buildOutcome(value) } })],
    });
  const withOutput = (output: string) =>
    buildCaseResult({ attempts: [buildAttempt(true, { output })] });
  const moduleSummary = (a: ReturnType<typeof buildRun>, b: typeof a) => {
    const diff = diffRuns(a, b);
    const byName = new Map(diff.tests.map((t) => [t.nodeid, t]));
    return summariseDiffModule([test], byName);
  };

  it("counts a module whose only change is a score regression as changed", () => {
    const a = buildRun("01", { [test]: { cases: { c1: withScore(0.9) } } });
    const b = buildRun("02", { [test]: { cases: { c1: withScore(0.5) } } });
    const summary = moduleSummary(a, b);

    expect(summary).toMatchObject({
      newlyFailing: 0,
      newlyPassing: 0,
      stillFailing: 0,
      scoresRegressed: 1,
    });
    expect(hasDiffChanges(summary)).toBe(true);
  });

  // The assertion passes on both sides, so nothing but the content counter can
  // report that the model answered differently.
  it("counts a rewritten output the verdict still accepts as changed", () => {
    const a = buildRun("01", {
      [test]: { cases: { c1: withOutput("Open Settings and pick a date.") } },
    });
    const b = buildRun("02", {
      [test]: {
        cases: { c1: withOutput("To restore, go to Settings, then Backups.") },
      },
    });
    const summary = moduleSummary(a, b);

    expect(summary).toMatchObject({ stillPassing: 1, contentChanged: 1 });
    expect(hasDiffChanges(summary)).toBe(true);
  });

  it("counts a case identical on both sides as unchanged", () => {
    const same = { c1: withOutput("Open Settings and pick a date.") };
    const a = buildRun("01", { [test]: { cases: same } });
    const b = buildRun("02", { [test]: { cases: same } });
    const summary = moduleSummary(a, b);

    expect(summary).toMatchObject({ stillPassing: 1, contentChanged: 0 });
    expect(hasDiffChanges(summary)).toBe(false);
  });

  it("counts a case still failing on both sides as something to open", () => {
    const failing = { c1: buildCaseResult({ outcome: "failed" }) };
    const a = buildRun("01", { [test]: { cases: failing } });
    const b = buildRun("02", { [test]: { cases: failing } });
    const summary = moduleSummary(a, b);

    expect(summary).toMatchObject({ stillFailing: 1 });
    expect(hasDiffChanges(summary)).toBe(true);
  });

  it("treats a module with only still-passing cases as unchanged", () => {
    const a = buildRun("01", { [test]: { cases: { c1: withScore(0.9) } } });
    const b = buildRun("02", { [test]: { cases: { c1: withScore(0.9) } } });
    const summary = moduleSummary(a, b);

    expect(summary).toMatchObject({ stillPassing: 1 });
    expect(hasDiffChanges(summary)).toBe(false);
  });

  it("opens a test on the same counters as its module", () => {
    const a = buildRun("01", { [test]: { cases: { c1: withScore(0.9) } } });
    const b = buildRun("02", { [test]: { cases: { c1: withScore(0.5) } } });
    const diff = diffRuns(a, b);
    const byName = new Map(diff.tests.map((t) => [t.nodeid, t]));

    expect(summariseTestDiff(byName.get(test)!)).toEqual(
      summariseDiffModule([test], byName),
    );
  });

  it("sums content changes across a module's tests", () => {
    const one = "tests/t.py::test_one";
    const two = "tests/t.py::test_two";
    const a = buildRun("01", {
      [one]: { cases: { c1: withOutput("short") } },
      [two]: { cases: { c1: withOutput("terse") } },
    });
    const b = buildRun("02", {
      [one]: { cases: { c1: withOutput("a longer answer") } },
      [two]: { cases: { c1: withOutput("a chattier answer") } },
    });
    const diff = diffRuns(a, b);
    const byName = new Map(diff.tests.map((t) => [t.nodeid, t]));

    expect(summariseDiffModule([one, two], byName).contentChanged).toBe(2);
  });

  it("sums a module of several tests to the run's own totals", () => {
    const one = "tests/t.py::test_one";
    const two = "tests/t.py::test_two";
    const a = buildRun("01", {
      [one]: { cases: { c1: buildCaseResult({ outcome: "passed" }) } },
      [two]: {
        outcome: "failed",
        cases: {
          c1: buildCaseResult({ outcome: "failed" }),
          c2: buildCaseResult({ outcome: "passed" }),
        },
      },
    });
    const b = buildRun("02", {
      [one]: { cases: { c1: buildCaseResult({ outcome: "failed" }) } },
      [two]: {
        outcome: "passed",
        cases: {
          c1: buildCaseResult({ outcome: "passed" }),
          c2: buildCaseResult({ outcome: "errored" }),
          c3: buildCaseResult(),
        },
      },
    });
    const diff = diffRuns(a, b);
    const byName = new Map(diff.tests.map((t) => [t.nodeid, t]));

    // The module holds every test, so its rollup is the run's total.
    const summary = summariseDiffModule([one, two], byName);
    expect(summary).toEqual({
      regressed: 1,
      recovered: 1,
      outcomeFlips: 1,
      newlyFailing: 1,
      newlyPassing: 1,
      errored: 1,
      contentChanged: 0,
      stillPassing: 0,
      stillFailing: 0,
      scoresRegressed: 0,
      scoresImproved: 0,
      onlyInEither: 1,
    });
    expect(summary).toMatchObject({
      regressed: diff.totals.testsRegressed,
      recovered: diff.totals.testsRecovered,
      outcomeFlips: diff.totals.outcomeFlips,
      newlyFailing: diff.totals.newlyFailing,
      newlyPassing: diff.totals.newlyPassing,
      errored: diff.totals.casesErrored,
      stillPassing: diff.totals.stillPassing,
      stillFailing: diff.totals.stillFailing,
      scoresRegressed: diff.totals.scoresRegressed,
      scoresImproved: diff.totals.scoresImproved,
      onlyInEither: diff.totals.casesOnlyInA + diff.totals.casesOnlyInB,
    });
  });
});
