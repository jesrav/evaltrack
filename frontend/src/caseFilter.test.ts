import { describe, it, expect } from "vitest";

import {
  NO_CASE_FILTER,
  filterCases,
  isCaseFilterActive,
  matchesCaseFilter,
} from "./caseFilter";
import { buildCaseResult } from "./test-support";
import type { CaseRecord } from "./types";

const filter = (opts: Partial<typeof NO_CASE_FILTER> = {}) => ({
  ...NO_CASE_FILTER,
  ...opts,
});

describe("matchesCaseFilter", () => {
  it("keeps every case at the filter defaults", () => {
    for (const outcome of ["passed", "failed", "errored"] as const) {
      expect(
        matchesCaseFilter(
          "anything",
          buildCaseResult({ outcome }),
          NO_CASE_FILTER,
        ),
      ).toBe(true);
    }
  });

  // An errored case reached no verdict, so it is not a failure. It is still the
  // kind of row someone opens "failing only" to find, so it has to survive.
  it("keeps errored cases under failing-only, not just failed ones", () => {
    const only = filter({ failingOnly: true });

    expect(
      matchesCaseFilter("c", buildCaseResult({ outcome: "failed" }), only),
    ).toBe(true);
    expect(
      matchesCaseFilter("c", buildCaseResult({ outcome: "errored" }), only),
    ).toBe(true);
    expect(
      matchesCaseFilter("c", buildCaseResult({ outcome: "passed" }), only),
    ).toBe(false);
  });

  it("matches a name substring without case", () => {
    const c = buildCaseResult({});

    expect(
      matchesCaseFilter("edge-empty-input", c, filter({ name: "EMPTY" })),
    ).toBe(true);
    expect(
      matchesCaseFilter("EDGE-EMPTY-INPUT", c, filter({ name: "empty" })),
    ).toBe(true);
    expect(
      matchesCaseFilter("acme-invoice", c, filter({ name: "empty" })),
    ).toBe(false);
  });

  // Typing into the box leaves spaces around a pasted name, and a lone space
  // must not empty the table.
  it("ignores surrounding spaces in the name filter", () => {
    expect(
      matchesCaseFilter(
        "acme-invoice",
        buildCaseResult({}),
        filter({ name: "  " }),
      ),
    ).toBe(true);
    expect(
      matchesCaseFilter(
        "acme-invoice",
        buildCaseResult({}),
        filter({ name: " acme " }),
      ),
    ).toBe(true);
  });

  it("requires both controls to match when both are set", () => {
    const both = filter({ failingOnly: true, name: "acme" });

    expect(
      matchesCaseFilter(
        "acme-invoice",
        buildCaseResult({ outcome: "failed" }),
        both,
      ),
    ).toBe(true);
    expect(
      matchesCaseFilter(
        "acme-invoice",
        buildCaseResult({ outcome: "passed" }),
        both,
      ),
    ).toBe(false);
    expect(
      matchesCaseFilter("other", buildCaseResult({ outcome: "failed" }), both),
    ).toBe(false);
  });
});

describe("isCaseFilterActive", () => {
  it("is inactive at the defaults and for a blank name", () => {
    expect(isCaseFilterActive(NO_CASE_FILTER)).toBe(false);
    expect(isCaseFilterActive(filter({ name: "   " }))).toBe(false);
  });

  it("is active once either control is set", () => {
    expect(isCaseFilterActive(filter({ failingOnly: true }))).toBe(true);
    expect(isCaseFilterActive(filter({ name: "acme" }))).toBe(true);
  });
});

describe("filterCases", () => {
  const cases: [string, CaseRecord][] = [
    ["acme-invoice", buildCaseResult({ outcome: "passed" })],
    ["edge-empty-input", buildCaseResult({ outcome: "failed" })],
    ["unicode-vendor", buildCaseResult({ outcome: "errored" })],
  ];

  it("keeps the incoming order of the surviving cases", () => {
    const kept = filterCases(cases, filter({ failingOnly: true }));

    expect(kept.map(([name]) => name)).toEqual([
      "edge-empty-input",
      "unicode-vendor",
    ]);
  });

  it("returns nothing when no case matches", () => {
    expect(filterCases(cases, filter({ name: "nope" }))).toEqual([]);
  });
});
