import { describe, it, expect } from "vitest";

import {
  ReportDataError,
  countCasesWithValuesLeftOut,
  parseReportData,
  showPreviewsOfValuesLeftOut,
  type ReportData,
} from "./reportData";
import { buildCaseResult, buildRun } from "./test-support";

const run = buildRun("01J9Z3QW2KJ5H8VN4TQY7B6MDC", {
  test_x: { outcome: "passed", cases: { c1: buildCaseResult() } },
});

/** A report as the CLI writes it, with every field the page reads. */
function reportJson(overrides: Partial<ReportData> = {}): string {
  const data: ReportData = {
    run: { run, via: null },
    baseline: null,
    refs: [],
    history: { reliability: {}, score_history: {} },
    mainline: null,
    mainline_error: null,
    pr_url_template: null,
    generated_at: "2026-01-01T00:00:00Z",
    generated_by: "0.3.0",
    ...overrides,
  };
  return JSON.stringify(data);
}

describe("parseReportData", () => {
  it("reads what the CLI embeds", () => {
    const data = parseReportData(
      reportJson({
        run: { run, via: "pr/12" },
        baseline: run,
        refs: [{ name: "pr/12", tip: null, kind: "other" }],
      }),
    );

    expect(data.run.run.id).toBe(run.id);
    expect(data.run.via).toBe("pr/12");
    expect(data.baseline?.id).toBe(run.id);
    expect(data.refs.map((r) => r.name)).toEqual(["pr/12"]);
    expect(data.generated_by).toBe("0.3.0");
  });

  it("decodes the escapes the CLI writes for the script element", () => {
    // `</script>` in an output is written as JSON escapes, which decode back
    // to the same text.
    const escaped = reportJson().replace(
      '"test_x"',
      '"\\u003c/script\\u003e \\u0026 \\u2028"',
    );

    const data = parseReportData(escaped);

    expect(Object.keys(data.run.run.tests)).toEqual(["</script> & \u2028"]);
  });

  it.each([
    ["a missing element", undefined],
    ["an empty element", "   "],
    ["text that is not JSON", "{not json"],
    ["JSON that is not a report", JSON.stringify({ tests: {} })],
    ["a run with no tests", JSON.stringify({ run: { run: { id: "x" } } })],
  ])("rejects %s with a message the page can show", (_what, text) => {
    expect(() => parseReportData(text)).toThrow(ReportDataError);
  });
});

describe("countCasesWithValuesLeftOut", () => {
  const envelope = (runId: string, field: string) => ({
    $deferred: {
      preview: "…",
      size: 20000,
      sha256: "0",
      run: runId,
      test: "test_x",
      case: "c1",
      field,
    },
  });
  const withLeftOut = (runId: string) =>
    buildRun(runId, {
      test_x: {
        outcome: "passed",
        cases: {
          c1: buildCaseResult({
            inputs: envelope(runId, "inputs"),
            expected_output: envelope(runId, "expected_output"),
          }),
        },
      },
    });

  it("counts a case once, however many values and runs it is missing in", () => {
    const data = parseReportData(
      reportJson({
        run: { run: withLeftOut("01J9Z3QW2KJ5H8VN4TQY7B6MDA"), via: null },
        baseline: withLeftOut("01J9Z3QW2KJ5H8VN4TQY7B6MDB"),
      }),
    );

    expect(countCasesWithValuesLeftOut(data)).toBe(1);
  });

  it("is zero for a run with every value in the page", () => {
    expect(countCasesWithValuesLeftOut(parseReportData(reportJson()))).toBe(0);
  });
});

describe("showPreviewsOfValuesLeftOut", () => {
  const leftOut = {
    $deferred: {
      preview: "The first words",
      size: 20480,
      sha256: "0",
      run: "01J9Z3QW2KJ5H8VN4TQY7B6MDC",
      test: "test_x",
      case: "c1",
      field: "output",
      attempt: 0,
    },
  };

  it("puts the first characters and the size where the value was", () => {
    const shown = showPreviewsOfValuesLeftOut({
      title: "c1",
      attempts: [{ output: leftOut }, { output: "short" }],
    });

    expect(shown).toEqual({
      title: "c1",
      attempts: [
        {
          output: "The first words… [20 KB in all, left out of the report]",
        },
        { output: "short" },
      ],
    });
  });

  it("returns the same content when nothing was left out", () => {
    const content = { title: "c1", attempts: [{ output: "short" }] };

    expect(showPreviewsOfValuesLeftOut(content)).toBe(content);
  });
});
