import { describe, it, expect } from "vitest";

import { diffRuns } from "../diff";
import { buildRun } from "../test-support";
import { testLevelChanges, unchangedCellValue } from "./RunDiff";

// A test that errored before evaluating on one side is a paired test with an
// outcome flip, not a removal. Only a test one run never had is listed here.
describe("testLevelChanges", () => {
  const test = "tests/t.py::test_eval";

  it("lists nothing for a test present in both runs, evaluated or not", () => {
    const a = buildRun("01", { [test]: { cases: {}, outcome: "passed" } });
    const b = buildRun("02", { [test]: { outcome: "errored" } });
    const changes = testLevelChanges(diffRuns(a, b), a, b);
    expect(changes).toEqual({ items: [], removed: 0, added: 0 });
  });

  it("keeps a test the other run never had as only on its side", () => {
    const a = buildRun("01", { [test]: { cases: {} } });
    const b = buildRun("02", {});
    const changes = testLevelChanges(diffRuns(a, b), a, b);
    expect(changes.items.map((c) => c.label)).toEqual(["only in BASE"]);
    expect(changes).toMatchObject({ removed: 1, added: 0 });
  });

  it("labels a test that errored before evaluating and is absent from the other run by its outcome", () => {
    const a = buildRun("01", { [test]: { outcome: "errored" } });
    const b = buildRun("02", {});
    const changes = testLevelChanges(diffRuns(a, b), a, b);
    expect(changes.items.map((c) => c.label)).toEqual([
      "only in BASE (errored)",
    ]);
    expect(changes).toMatchObject({ removed: 1, added: 0 });
  });

  it("counts a test only COMPARE has as added", () => {
    const a = buildRun("01", {});
    const b = buildRun("02", { [test]: { cases: {} } });
    const changes = testLevelChanges(diffRuns(a, b), a, b);
    expect(changes.items.map((c) => c.label)).toEqual(["only in COMPARE"]);
    expect(changes).toMatchObject({ removed: 0, added: 1 });
  });
});

// A case added or removed inside a test sits on one side of the row only, so
// its cell counts as unchanged and would render an em dash if the absent side
// were the one displayed.
describe("unchangedCellValue", () => {
  it("shows BASE's value when both runs recorded one", () => {
    expect(unchangedCellValue("same", "same")).toBe("same");
  });

  it("shows COMPARE's value for a case added in COMPARE", () => {
    expect(unchangedCellValue(undefined, "added")).toBe("added");
  });

  it("shows BASE's value for a case removed in COMPARE", () => {
    expect(unchangedCellValue("removed", undefined)).toBe("removed");
  });
});
