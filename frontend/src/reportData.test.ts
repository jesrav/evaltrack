import { describe, it, expect } from "vitest";

import {
  ReportDataError,
  countCasesWithValuesLeftOut,
  parseReportData,
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
    against: null,
    against_error: null,
    refs: [],
    history: { reliability: {}, score_history: {} },
    mainline: null,
    history_error: null,
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
        against: { run, via: "baseline" },
        refs: [{ name: "pr/12", tip: null, kind: "other" }],
      }),
    );

    expect(data.run.run.id).toBe(run.id);
    expect(data.run.via).toBe("pr/12");
    expect(data.against?.run.id).toBe(run.id);
    expect(data.against?.via).toBe("baseline");
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

  it("counts a case once, however many values and sides it is missing on", () => {
    const data = parseReportData(
      reportJson({
        run: { run: withLeftOut("01J9Z3QW2KJ5H8VN4TQY7B6MDA"), via: null },
        against: { run: withLeftOut("01J9Z3QW2KJ5H8VN4TQY7B6MDB"), via: null },
      }),
    );

    expect(countCasesWithValuesLeftOut(data)).toBe(1);
  });

  it("is zero for a run with every value in the page", () => {
    expect(countCasesWithValuesLeftOut(parseReportData(reportJson()))).toBe(0);
  });
});
