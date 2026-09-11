import { describe, it, expect } from "vitest";

import {
  buildAnchorKey,
  buildCaseSections,
  focusAnchor,
  findFocusedSection,
  sectionOfKind,
} from "./caseSections";
import { buildAttempt, buildCaseResult, buildOutcome } from "./test-support";
import type { CaseSectionId } from "./caseSections";

const ids = (
  c: Parameters<typeof buildCaseSections>[0],
  i = 0,
): CaseSectionId[] => buildCaseSections(c, c.attempts[i]).map((s) => s.id);

describe("buildCaseSections", () => {
  // The point of one drawer per case is that a reader scrolls the recorded
  // fields in one fixed order, whatever the case carries.
  it("orders the sections the same way for every case", () => {
    const c = buildCaseResult({
      inputs: "ask",
      expected_output: "answer",
      metadata: { tier: "gold" },
      attempts: [
        buildAttempt(true, {
          output: "answer",
          results: {
            quality: buildOutcome(0.8),
            correct: buildOutcome(true),
          },
        }),
      ],
    });

    expect(ids(c)).toEqual([
      "input",
      "comparison",
      "metadata",
      "scores",
      "assertions",
    ]);
  });

  it("leaves out the sections the case recorded nothing for", () => {
    const c = buildCaseResult({ inputs: "ask" });

    expect(ids(c)).toEqual(["input"]);
  });

  // The pair is what a failed comparison is about, so it shows as one section
  // rather than two the reader has to hold together.
  it("pairs the expected and produced values in one section", () => {
    const c = buildCaseResult({
      expected_output: "answer",
      attempts: [buildAttempt(false, { output: "other" })],
    });
    const [section] = buildCaseSections(c, c.attempts[0]);

    expect(section).toEqual({ id: "comparison", label: "Expected and output" });
  });

  it("shows the expected value alone when the attempt produced none", () => {
    const c = buildCaseResult({ expected_output: "answer" });
    const [section] = buildCaseSections(c, c.attempts[0]);

    expect(section).toEqual({ id: "expected", label: "Expected" });
  });

  it("shows the output alone when the case has no expected value", () => {
    const c = buildCaseResult({
      attempts: [buildAttempt(true, { output: "answer" })],
    });

    expect(ids(c)).toEqual(["output"]);
  });

  // An errored attempt reached no verdict, so its output, scores and assertions
  // say nothing. Showing them empty suggests the case was judged.
  it("replaces the verdict sections with the error for an errored attempt", () => {
    const c = buildCaseResult({
      outcome: "errored",
      inputs: "ask",
      attempts: [
        buildAttempt(false, {
          outcome: "errored",
          output: "half an answer",
          results: { quality: buildOutcome(0) },
        }),
      ],
    });

    expect(ids(c)).toEqual(["input", "error"]);
  });

  it("follows the attempt on screen", () => {
    const c = buildCaseResult({
      outcome: "passed",
      attempts: [
        buildAttempt(false, { outcome: "errored" }),
        buildAttempt(true, { output: "answer" }),
      ],
    });

    expect(ids(c, 0)).toEqual(["error"]);
    expect(ids(c, 1)).toEqual(["output"]);
  });
});

describe("sectionOfKind", () => {
  // The run table, the case inspector and the diff each group results by kind,
  // and a click carries the kind's section, so a section has to exist for every
  // kind a case can record.
  it("names a section that a case carrying that kind has", () => {
    const attempt = buildAttempt(true, {
      results: {
        quality: buildOutcome(0.8),
        correct: buildOutcome(true),
      },
    });
    const sections = buildCaseSections(
      buildCaseResult({ attempts: [attempt] }),
      attempt,
    );

    for (const kind of ["score", "assertion"] as const) {
      const section = sectionOfKind(kind);
      expect(findFocusedSection(sections, { section })).toBe(section);
    }
  });
});

describe("findFocusedSection", () => {
  const sections = buildCaseSections(
    buildCaseResult({
      expected_output: "answer",
      attempts: [buildAttempt(true, { output: "answer" })],
    }),
    buildAttempt(true, { output: "answer" }),
  );

  // The output cell and the expected cell are two columns of one section, so
  // both have to land somewhere.
  it("sends a click on either paired value to the shared section", () => {
    expect(findFocusedSection(sections, { section: "output" })).toBe(
      "comparison",
    );
    expect(findFocusedSection(sections, { section: "expected" })).toBe(
      "comparison",
    );
  });

  it("lands nowhere when the case has no such section", () => {
    expect(findFocusedSection(sections, { section: "metadata" })).toBe(null);
    expect(findFocusedSection(sections, undefined)).toBe(null);
  });
});

describe("focusAnchor", () => {
  const shown = buildAttempt(false, {
    output: "answer",
    results: { correct: buildOutcome(false) },
  });
  const c = buildCaseResult({ outcome: "failed", attempts: [shown] });
  const sections = buildCaseSections(c, shown);

  it("reaches the clicked evaluator, not only its list", () => {
    expect(
      focusAnchor(sections, shown, { section: "assertions", name: "correct" }),
    ).toBe(buildAnchorKey("assertions", "correct"));
  });

  // Attempts of one case can run different evaluators, so the clicked name can
  // be missing from the attempt the drawer opens on.
  it("falls back to the list when the attempt lost that evaluator", () => {
    expect(
      focusAnchor(sections, shown, { section: "assertions", name: "gone" }),
    ).toBe(buildAnchorKey("assertions"));
  });
});
