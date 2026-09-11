import { describe, it, expect } from "vitest";

import { otherKeys } from "./OutcomeCard";

describe("otherKeys", () => {
  it("leaves out the fields that every serialised result carries", () => {
    // `verdict` and the three bars arrive on every result, so a card that
    // lists them shows an "other" tree of nulls under every evaluator.
    const result = {
      value: 0.5,
      verdict: null,
      bar: null,
      runner_bar: null,
      marker_bar: null,
      reason: null,
      evaluator: null,
      details: {},
    };
    expect(otherKeys(result)).toEqual([]);
  });

  it("keeps what a runner recorded beyond the schema", () => {
    const result = {
      value: true,
      reason: "matched",
      evaluator: null,
      details: { answer: "42" },
      evidence: ["line 3"],
      judge_model: "gpt",
    };
    expect(otherKeys(result)).toEqual(["evidence", "judge_model"]);
  });
});
