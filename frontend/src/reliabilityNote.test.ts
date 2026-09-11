import { describe, it, expect } from "vitest";

import { buildReliabilityNoteLines } from "./reliabilityNote";
import { buildReliabilityPoint } from "./test-support";
import type { CaseReliability } from "./types";

const rel = (opts: Partial<CaseReliability> = {}): CaseReliability => ({
  pooled_attempts: 10,
  pooled_passes: 8,
  rate: 0.8,
  lower_bound: 0.49,
  target: 0.95,
  below_target: false,
  pooled_runs: 8,
  eval_version: null,
  points: [],
  diverged: false,
  ...opts,
});

// The note is the only place the reader gets the pool size, the target and the
// lower bound together. Each line has to say one of them, and the verdict has to
// match the chip beside it.
describe("reliability note lines", () => {
  it("leads with the pool and the target", () => {
    expect(buildReliabilityNoteLines(rel(), 20)[0]).toEqual({
      label: "Mainline",
      text: "8/10 attempts across 8 runs · target 95%",
    });
  });

  it("drops the target from the pool line when the eval sets none", () => {
    const lines = buildReliabilityNoteLines(rel({ target: null }), 20);
    expect(lines[0]?.text).toBe("8/10 attempts across 8 runs");
    // No target means nothing to pass or fail, so the lower bound stands alone.
    expect(lines[1]?.text).toBe("49% at 95% confidence");
  });

  it("warns when the samples cannot confirm the target", () => {
    // The chip reads the rate against the target alone, so this line is the
    // reader's only sign that the pool is still too small to back it.
    expect(buildReliabilityNoteLines(rel(), 20)[1]).toEqual({
      label: "Lower bound",
      text: "49% at 95% confidence. Too few samples to confirm the target yet",
    });
  });

  it("says the target is met once the floor clears it", () => {
    const lines = buildReliabilityNoteLines(rel({ lower_bound: 0.96 }), 20);
    expect(lines[1]?.text).toBe("96% at 95% confidence. The target is met");
  });

  it("compares the floor to the target raw, at the boundary", () => {
    // A floor exactly at the target meets it; one a fraction under does not,
    // whatever the two figures floor to on screen.
    expect(
      buildReliabilityNoteLines(rel({ lower_bound: 0.95 }), 20)[1]?.text,
    ).toBe("95% at 95% confidence. The target is met");
    expect(
      buildReliabilityNoteLines(rel({ lower_bound: 0.9499 }), 20)[1]?.text,
    ).toBe("94% at 95% confidence. Too few samples to confirm the target yet");
  });

  it("says the rate is below the target ahead of anything about the floor", () => {
    // A failing rate is the one state that alarms, so it wins the line.
    const lines = buildReliabilityNoteLines(
      rel({ rate: 0.5, below_target: true }),
      20,
    );
    expect(lines[1]?.text).toBe(
      "49% at 95% confidence. The rate is below the target",
    );
  });

  it("counts one attempt and one run in the singular", () => {
    const lines = buildReliabilityNoteLines(
      rel({ pooled_attempts: 1, pooled_passes: 1, pooled_runs: 1 }),
      20,
    );
    expect(lines[0]?.text).toBe("1/1 attempt across 1 run · target 95%");
  });

  it("says how many runs the bars show when the pool is larger", () => {
    const points = Array.from({ length: 3 }, (_, i) =>
      buildReliabilityPoint(`r${i}`, [true]),
    );
    expect(buildReliabilityNoteLines(rel({ points }), 2).at(-1)).toEqual({
      label: null,
      text: "The bars show the 2 most recent runs.",
    });
    // Within the cap there is nothing to explain.
    expect(buildReliabilityNoteLines(rel({ points }), 3)).toHaveLength(2);
  });

  it("explains the outlined bars only when the viewed run is off mainline", () => {
    const points = [
      buildReliabilityPoint("r1", [true]),
      buildReliabilityPoint("r2", [false], { off_mainline: true }),
    ];
    expect(buildReliabilityNoteLines(rel({ points }), 20).at(-1)).toEqual({
      label: null,
      text: "This run is not on mainline. Its attempts are the outlined bars.",
    });
    expect(
      buildReliabilityNoteLines(rel({ points: points.slice(0, 1) }), 20),
    ).toHaveLength(2);
  });
});
