import { describe, it, expect } from "vitest";

import { NO_CASE_FILTER } from "../caseFilter";
import { groupTestsByModule } from "../grouping";
import {
  buildAttempt,
  buildCaseResult,
  buildReliabilityPoint,
  buildRun,
} from "../test-support";
import type { CaseReliability } from "../types";
import {
  attemptShowsResults,
  countCases,
  describeRunners,
  layoutSparkline,
  reliabilityChipState,
  runVerdictKind,
  showsExpectedColumn,
  shownModules,
  summariseModule,
  summariseRun,
} from "./RunDetail";

// The header verdict, the module badges and the rendered rows all count the same
// tests. A disagreement makes every count on the page suspect, so these tests
// hold them together.
describe("module and run rollups agree", () => {
  const mixed = buildRun("01", {
    t_fail: {
      module: "tests/a.py",
      outcome: "failed",
      cases: { c1: buildCaseResult({ outcome: "failed" }) },
    },
    t_pass: {
      module: "tests/a.py",
      outcome: "passed",
      cases: { c1: buildCaseResult({ outcome: "passed" }) },
    },
    // Errored before evaluate(), so there is no report to count from.
    t_boom: { module: "tests/a.py", outcome: "errored" },
    // Skipped by `skipif`, and still inside the header's total.
    t_skip: { module: "tests/b.py", outcome: "skipped" },
  });

  it("counts the same total tests as the run header", () => {
    const groups = groupTestsByModule(mixed);
    const total = groups.reduce(
      (n, g) => n + summariseModule(g, mixed).total,
      0,
    );

    expect(total).toBe(summariseRun(mixed).tests);
    expect(total).toBe(4);
  });

  it("counts the same failing tests as the run header", () => {
    const groups = groupTestsByModule(mixed);
    const failing = groups.reduce(
      (n, g) => n + summariseModule(g, mixed).failing,
      0,
    );

    expect(failing).toBe(summariseRun(mixed).failingTests);
    expect(failing).toBe(2);
  });

  it("counts the same passing tests as the run header", () => {
    const groups = groupTestsByModule(mixed);
    const passing = groups.reduce(
      (n, g) => n + summariseModule(g, mixed).passing,
      0,
    );

    expect(passing).toBe(summariseRun(mixed).passingTests);
    expect(passing).toBe(1);
  });

  it("counts every test it renders", () => {
    for (const group of groupTestsByModule(mixed)) {
      const rendered = group.tests.length + group.unevaluatedTests.length;
      expect(summariseModule(group, mixed).total).toBe(rendered);
    }
  });

  it("reports a skipped eval as skipped, not as a pass or a failure", () => {
    const group = groupTestsByModule(mixed).find(
      (g) => g.module === "tests/b.py",
    )!;

    expect(summariseModule(group, mixed)).toEqual({
      total: 1,
      failing: 0,
      xfailed: 0,
      passing: 0,
      skipped: 1,
    });
  });
});

// A skipped test records no outcome. The header must not report "passed" when
// nothing failed, because then a suite whose evals never ran shows green.
describe("run verdict", () => {
  it("gives an all-skipped run no verdict", () => {
    const skipped = buildRun("01", {
      t_a: { module: "tests/a.py", outcome: "skipped" },
      t_b: { module: "tests/a.py", outcome: "skipped" },
    });
    const stats = summariseRun(skipped);

    expect(stats).toMatchObject({ tests: 2, failingTests: 0, passingTests: 0 });
    expect(runVerdictKind(stats)).toBe("none");
  });

  it("reads as passed when one test skipped and another passed", () => {
    const partial = buildRun("01", {
      t_skip: { module: "tests/a.py", outcome: "skipped" },
      t_pass: {
        module: "tests/a.py",
        outcome: "passed",
        cases: { c1: buildCaseResult({ outcome: "passed" }) },
      },
    });
    const stats = summariseRun(partial);

    expect(stats).toMatchObject({ tests: 2, failingTests: 0, passingTests: 1 });
    expect(runVerdictKind(stats)).toBe("passed");
  });

  it("reads as failed when a test failed, whatever else skipped", () => {
    const failed = buildRun("01", {
      t_skip: { module: "tests/a.py", outcome: "skipped" },
      t_fail: {
        module: "tests/a.py",
        outcome: "failed",
        cases: { c1: buildCaseResult({ outcome: "failed" }) },
      },
    });

    expect(runVerdictKind(summariseRun(failed))).toBe("failed");
  });
});

// The filter narrows the rows, never the rollups. A module badge that follows
// the filter tells the reader their suite got better as they typed.
describe("filtered run view", () => {
  const mixed = buildRun("01", {
    t_extract: {
      module: "evals/a.py",
      outcome: "failed",
      cases: {
        "acme-invoice": buildCaseResult({ outcome: "passed" }),
        "edge-empty-input": buildCaseResult({ outcome: "failed" }),
      },
    },
    t_green: {
      module: "evals/b.py",
      outcome: "passed",
      cases: { ok: buildCaseResult({ outcome: "passed" }) },
    },
    t_boom: { module: "evals/b.py", outcome: "errored" },
  });
  const filter = (opts: Partial<typeof NO_CASE_FILTER>) => ({
    ...NO_CASE_FILTER,
    ...opts,
  });

  it("draws every module and test when nothing is filtered", () => {
    const modules = shownModules(mixed, NO_CASE_FILTER);

    expect(modules.map((m) => m.module)).toEqual(["evals/a.py", "evals/b.py"]);
    expect(modules[1]!.tests.map((t) => t.nodeid)).toEqual(["t_green"]);
    expect(modules[1]!.unevaluatedTests).toEqual(["t_boom"]);
  });

  it("drops a module whose every case is filtered out", () => {
    const modules = shownModules(mixed, filter({ name: "acme" }));

    expect(modules.map((m) => m.module)).toEqual(["evals/a.py"]);
    expect(modules[0]!.tests[0]!.cases.map(([name]) => name)).toEqual([
      "acme-invoice",
    ]);
  });

  it("keeps the module badge counting the whole module", () => {
    const [first] = shownModules(mixed, filter({ name: "acme" }));

    // The one row left is a pass, and the badge still reports the failure.
    expect(first!.summary.failing).toBe(1);
    expect(first!.summary.total).toBe(1);
  });

  it("tells the table how many rows the filter took away", () => {
    const [first] = shownModules(mixed, filter({ failingOnly: true }));

    expect(first!.tests[0]!.cases).toHaveLength(1);
    expect(first!.tests[0]!.totalCases).toBe(2);
  });

  // A test that errored before evaluate() has no case row to match on, and it
  // is exactly the kind of failure "failing only" is opened to find.
  it("keeps an errored test with no eval under failing-only", () => {
    const modules = shownModules(mixed, filter({ failingOnly: true }));
    const second = modules.find((m) => m.module === "evals/b.py");

    expect(second!.tests).toEqual([]);
    expect(second!.unevaluatedTests).toEqual(["t_boom"]);
  });

  it("drops a test with no eval once a name is typed", () => {
    const modules = shownModules(mixed, filter({ name: "edge" }));

    expect(modules.map((m) => m.module)).toEqual(["evals/a.py"]);
  });

  it("counts shown cases against the run's own total", () => {
    expect(countCases(mixed, NO_CASE_FILTER)).toEqual({ shown: 3, total: 3 });
    expect(countCases(mixed, filter({ failingOnly: true }))).toEqual({
      shown: 1,
      total: 3,
    });
    // The total never moves, so the note can never overstate the suite.
    expect(countCases(mixed, filter({ name: "nothing" }))).toEqual({
      shown: 0,
      total: 3,
    });
  });
});

// The rate pools mainline only, and the viewed run is drawn beside it. These
// guard against merging the two into one number. The strip must show the run
// without the layout implying that it is history.
describe("reliability sparkline layout", () => {
  it("draws the viewed run after the mainline it is not pooled with", () => {
    const layout = layoutSparkline([
      buildReliabilityPoint("r1", [true]),
      buildReliabilityPoint("r2", [true]),
      buildReliabilityPoint("viewed", [false, false], { off_mainline: true }),
    ]);

    expect(layout.bars.map((b) => b.off_mainline)).toEqual([
      false,
      false,
      true,
      true,
    ]);
    // The "N most recent of M" label counts mainline runs, so the overlay must
    // stay out of it.
    expect(layout.mainlineRuns).toBe(2);
    expect(layout.shown).toBe(2);
    expect(layout.hasOverlay).toBe(true);
  });

  it("separates the viewed run by a wider gap than it separates runs", () => {
    const [, , overlayBar] = layoutSparkline([
      buildReliabilityPoint("r1", [true]),
      buildReliabilityPoint("r2", [true]),
      buildReliabilityPoint("viewed", [true], { off_mainline: true }),
    ]).bars;
    const [, betweenRuns] = layoutSparkline([
      buildReliabilityPoint("r1", [true]),
      buildReliabilityPoint("r2", [true]),
      buildReliabilityPoint("r3", [true]),
    ]).bars;

    expect(overlayBar!.x).toBeGreaterThan(betweenRuns!.x);
  });

  it("keeps only the most recent runs, and the overlay regardless", () => {
    const many = Array.from({ length: 25 }, (_, i) =>
      buildReliabilityPoint(`r${i}`, [true]),
    );
    const layout = layoutSparkline([
      ...many,
      buildReliabilityPoint("viewed", [false], { off_mainline: true }),
    ]);

    expect(layout.shown).toBe(20);
    expect(layout.mainlineRuns).toBe(25);
    // 20 mainline bars kept plus the viewed run, and the newest mainline run
    // survives the cut (the oldest are the ones dropped).
    expect(layout.bars).toHaveLength(21);
    expect(layout.bars.at(-1)!.off_mainline).toBe(true);
    expect(layout.bars.at(-2)!.runId).toBe("r24");
  });

  it("has no overlay when the viewed run is itself a mainline run", () => {
    const layout = layoutSparkline([
      buildReliabilityPoint("r1", [true]),
      buildReliabilityPoint("r2", [true, false]),
    ]);

    expect(layout.hasOverlay).toBe(false);
    expect(layout.bars.every((b) => !b.off_mainline)).toBe(true);
    expect(layout.bars).toHaveLength(3);
  });
});

// The chip's whole claim is the rate against its target. The confidence floor
// belongs to the ⓘ note: were it to reach the chip again, a young eval would be
// back to spending its first runs in a third state the reader has to learn.
describe("reliability chip state", () => {
  const rel = (opts: Partial<CaseReliability>): CaseReliability => ({
    pooled_attempts: 8,
    pooled_passes: 8,
    rate: 1,
    lower_bound: 0.67,
    target: 0.95,
    below_target: false,
    pooled_runs: 8,
    eval_version: null,
    points: [],
    diverged: false,
    ...opts,
  });

  it("is ok once the rate clears its target", () => {
    expect(reliabilityChipState(rel({ lower_bound: 0.96 }))).toBe("ok");
  });

  it("keeps a rate under its target failing, however wide the interval", () => {
    expect(reliabilityChipState(rel({ rate: 0.8, below_target: true }))).toBe(
      "fail",
    );
  });

  it("stays neutral with no target, whatever the floor says", () => {
    expect(reliabilityChipState(rel({ target: null, lower_bound: 0.1 }))).toBe(
      "neutral",
    );
  });

  it("leaves the chip alone whichever side of the target the floor falls", () => {
    // 8/8 against a 95% target: the rate clears it and the floor (67%) does not,
    // which the chip no longer distinguishes from a settled 8/8.
    expect(reliabilityChipState(rel({}))).toBe("ok");
    expect(reliabilityChipState(rel({ lower_bound: 0.1 }))).toBe("ok");
    expect(reliabilityChipState(rel({ lower_bound: null }))).toBe("ok");
  });
});

// A comparison eval that passes every case records the same value as its target
// and as its output. Printing both is the widest pair of columns in the table,
// so the column has to earn its width.
describe("expected column", () => {
  it("stays hidden when no case was written against a target", () => {
    expect(showsExpectedColumn([buildCaseResult(), buildCaseResult()])).toBe(
      false,
    );
  });

  it("stays hidden when every target matches what the run produced", () => {
    const cases = [
      buildCaseResult({
        expected_output: { vendor: "ACME" },
        attempts: [buildAttempt(true, { output: { vendor: "ACME" } })],
      }),
      buildCaseResult({
        expected_output: "hi",
        attempts: [buildAttempt(true, { output: "hi" })],
      }),
    ];
    expect(showsExpectedColumn(cases)).toBe(false);
  });

  it("shows as soon as one case diverges", () => {
    const cases = [
      buildCaseResult({
        expected_output: { vendor: "ACME" },
        attempts: [buildAttempt(true, { output: { vendor: "ACME" } })],
      }),
      buildCaseResult({
        outcome: "failed",
        expected_output: { vendor: "ACME" },
        attempts: [buildAttempt(false, { output: { vendor: "UNKNOWN" } })],
      }),
    ];
    expect(showsExpectedColumn(cases)).toBe(true);
  });

  it("ignores key order, which carries no meaning", () => {
    const cases = [
      buildCaseResult({
        expected_output: { a: 1, b: 2 },
        attempts: [buildAttempt(true, { output: { b: 2, a: 1 } })],
      }),
    ];
    expect(showsExpectedColumn(cases)).toBe(false);
  });

  it("shows for a case that produced no output to compare", () => {
    const cases = [
      buildCaseResult({
        outcome: "errored",
        expected_output: { vendor: "ACME" },
        attempts: [],
      }),
    ];
    expect(showsExpectedColumn(cases)).toBe(true);
  });
});

describe("describeRunners", () => {
  // The runner is recorded per test, because one session can record several.
  // The run header names all of them rather than assuming one.
  it("lists each distinct runner once", () => {
    const run = buildRun("01RUN", {
      "tests/test_x.py::test_a": { outcome: "passed" },
      "tests/test_x.py::test_b": { outcome: "passed" },
    });
    expect(describeRunners(run)).toEqual(["pydantic-evals 0.0.0"]);
  });

  it("has nothing to say for a run whose tests never evaluated", () => {
    const run = buildRun("01RUN", {
      "tests/test_x.py::test_a": { outcome: "passed" },
    });
    for (const test of Object.values(run.tests)) test.runner = null;
    expect(describeRunners(run)).toEqual([]);
  });
});

describe("attemptShowsResults", () => {
  // The drawer drops the scores and assertions of an errored attempt, so a pill
  // in the row would open it with nothing to scroll to.
  it("hides the pills of an errored attempt that still recorded results", () => {
    const errored = buildAttempt(false, {
      outcome: "errored",
      results: {
        accuracy: {
          value: 0.5,
          reason: null,
          evaluator: { name: "e", arguments: null },
        },
      },
    });
    expect(attemptShowsResults(errored)).toBe(false);
    expect(attemptShowsResults(buildAttempt(false))).toBe(true);
  });
});
