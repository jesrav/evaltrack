import { describe, expect, it } from "vitest";
import { summariseErrors } from "./attemptErrors";

describe("summariseErrors", () => {
  it("has nothing to say about a clean attempt", () => {
    expect(summariseErrors([])).toBeNull();
    expect(summariseErrors(undefined)).toBeNull();
  });

  it("names the evaluator that raised, because the message alone does not", () => {
    expect(
      summariseErrors([{ message: "judge API down", evaluator: "Judge" }]),
    ).toBe("Judge: judge API down");
  });

  it("leads with the task, which is why the evaluator had nothing to judge", () => {
    const line = summariseErrors([
      { message: "judge down", evaluator: "Judge" },
      { message: "RuntimeError: boom", evaluator: null },
    ]);
    expect(line).toBe("RuntimeError: boom");
    expect(line).not.toContain("more");
  });

  it("counts the evaluators it does not have room to name", () => {
    expect(
      summariseErrors([
        { message: "judge API down", evaluator: "Judge" },
        { message: "no rubric", evaluator: "Rubric" },
      ]),
    ).toBe("Judge: judge API down (+1 more)");
  });
});
