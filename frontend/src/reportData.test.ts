import { describe, it, expect } from "vitest";

import { ReportDataError, parseReportData } from "./reportData";
import { buildCaseResult, buildRun } from "./test-support";

const run = buildRun("01J9Z3QW2KJ5H8VN4TQY7B6MDC", {
  test_x: { outcome: "passed", cases: { c1: buildCaseResult() } },
});

describe("parseReportData", () => {
  it("reads what the CLI embeds", () => {
    const data = parseReportData(
      JSON.stringify({
        run,
        via: "pr/12",
        against: run,
        against_via: "baseline",
        refs: [{ name: "pr/12", tip: null, kind: "other" }],
        history: { reliability: {}, score_history: {} },
        mainline: null,
        generated_at: "2026-01-01T00:00:00Z",
        generated_by: "0.3.0",
      }),
    );

    expect(data.run.id).toBe(run.id);
    expect(data.via).toBe("pr/12");
    expect(data.against?.id).toBe(run.id);
    expect(data.against_via).toBe("baseline");
    expect(data.refs.map((r) => r.name)).toEqual(["pr/12"]);
    expect(data.generated_by).toBe("0.3.0");
  });

  it("fills in the parts a page can leave out", () => {
    // A run alone is a report. Everything else has a meaning for "absent",
    // so the page never reads an undefined field.
    const data = parseReportData(JSON.stringify({ run }));

    expect(data.via).toBeNull();
    expect(data.against).toBeNull();
    expect(data.mainline).toBeNull();
    expect(data.refs).toEqual([]);
    expect(data.pr_url_template).toBeNull();
    expect(data.history_error).toBeNull();
    expect(data.history).toEqual({ reliability: {}, score_history: {} });
  });

  it("decodes the escapes the CLI writes for the script element", () => {
    // `</script>` in an output is written as JSON escapes, which decode back
    // to the same text.
    const escaped = JSON.stringify({ run }).replace(
      '"test_x"',
      '"\\u003c/script\\u003e \\u0026 \\u2028"',
    );

    const data = parseReportData(escaped);

    expect(Object.keys(data.run.tests)).toEqual(["</script> & \u2028"]);
  });

  it.each([
    ["a missing element", undefined],
    ["an empty element", "   "],
    ["text that is not JSON", "{not json"],
    ["JSON that is not a report", JSON.stringify({ tests: {} })],
    ["a run with no tests", JSON.stringify({ run: { id: "x" } })],
    ["a comparison that is not a run", JSON.stringify({ run, against: 3 })],
  ])("rejects %s with a message the page can show", (_what, text) => {
    expect(() => parseReportData(text)).toThrow(ReportDataError);
  });
});

it("keeps the reason the history is missing", () => {
  const data = parseReportData(
    JSON.stringify({ run, history_error: "no route to host" }),
  );

  expect(data.history_error).toBe("no route to host");
  expect(data.history).toEqual({ reliability: {}, score_history: {} });
});
