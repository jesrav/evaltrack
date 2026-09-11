import { describe, it, expect } from "vitest";

import {
  UNKNOWN_MODULE,
  groupTestsByModule,
  groupNodeidsByModule,
} from "./grouping";
import { buildRun } from "./test-support";

describe("groupTestsByModule", () => {
  it("groups tests by module path, sorted, with unknown module last", () => {
    const r = buildRun("01", {
      tb: { module: "tests/b.py", cases: {} },
      ta: { module: "tests/a.py", cases: {} },
      t2: { module: null, cases: {} },
    });

    const groups = groupTestsByModule(r);

    expect(groups.map((g) => g.module)).toEqual([
      "tests/a.py",
      "tests/b.py",
      UNKNOWN_MODULE,
    ]);
  });

  it("places every test under its module and sorts tests within a group", () => {
    const r = buildRun("01", {
      t2: { module: "tests/a.py", cases: {} },
      t1: { module: "tests/a.py", cases: {} },
    });

    const group = groupTestsByModule(r)[0]!;

    expect(group.module).toBe("tests/a.py");
    expect(group.tests).toEqual(["t1", "t2"]);
  });

  it("surfaces a failed/errored test with no report under its module", () => {
    const r = buildRun("01", {
      t_ok: { module: "tests/a.py", cases: {} },
      t_boom: { module: "tests/a.py", outcome: "errored" },
    });

    const group = groupTestsByModule(r)[0]!;

    expect(group.module).toBe("tests/a.py");
    expect(group.tests).toEqual(["t_ok"]);
    expect(group.unevaluatedTests).toEqual(["t_boom"]);
  });

  it("includes a skipped eval, which the run-level totals already count", () => {
    // An eval skipped by `skipif`, for a missing API key, records no report.
    // The run header still counts it, so the module has to show it too.
    const r = buildRun("01", {
      t_skip: { module: "tests/a.py", outcome: "skipped" },
    });

    const group = groupTestsByModule(r)[0]!;

    expect(group.tests).toEqual([]);
    expect(group.unevaluatedTests).toEqual(["t_skip"]);
  });

  it("keeps every recorded test, so module counts can match the run total", () => {
    const r = buildRun("01", {
      t_pass: { module: "tests/a.py", outcome: "passed" },
      t_none: { module: "tests/a.py", outcome: null },
    });

    const group = groupTestsByModule(r)[0]!;

    expect(group.unevaluatedTests).toEqual(["t_none", "t_pass"]);
  });
});

describe("groupNodeidsByModule", () => {
  it("resolves each test's module from the provided test records", () => {
    const r = buildRun("01", {
      t1: { module: "tests/a.py", cases: {} },
      t2: { module: null, cases: {} },
    });

    const groups = groupNodeidsByModule(["t1", "t2"], r.tests);

    expect(groups).toEqual([
      {
        module: "tests/a.py",
        tests: ["t1"],
        unevaluatedTests: [],
      },
      {
        module: UNKNOWN_MODULE,
        tests: ["t2"],
        unevaluatedTests: [],
      },
    ]);
  });
});
