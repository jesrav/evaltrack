import { describe, it, expect } from "vitest";

import { lineDiff } from "./TextDiff";

/** An output shaped like an agent transcript, one long line per step with every
 *  line different from the last, which is what makes a pair of them expensive. */
function transcript(lines: number, seed: number): string {
  const out: string[] = [];
  let s = seed;
  for (let i = 0; i < lines; i++) {
    s = (s * 1103515245 + 12345) % 2147483648;
    out.push(
      `[${String(i).padStart(5, "0")}] tool_call step=${s % 1000} ` +
        `payload={"id": "${s.toString(36)}", "text": "the quick brown fox"}`,
    );
  }
  return out.join("\n") + "\n";
}

describe("lineDiff", () => {
  it("diffs a pair a reader can follow", () => {
    const parts = lineDiff("one\ntwo\nthree\n", "one\nTWO\nthree\n");

    expect(parts?.map((p) => [p.removed === true, p.added === true])).toEqual([
      [false, false],
      [true, false],
      [false, true],
      [false, false],
    ]);
  });

  it("still diffs a long transcript that changed in places", () => {
    const a = transcript(12000, 1).split("\n");
    const b = a.map((line, i) => (i < 500 ? `${line} CHANGED` : line));

    expect(lineDiff(a.join("\n"), b.join("\n"))).not.toBeNull();
  });

  // Without the cap this pair takes minutes, so a regression shows up as a
  // timeout rather than a wrong answer.
  it("gives up on two outputs that share no lines", () => {
    expect(lineDiff(transcript(12000, 1), transcript(12000, 999))).toBeNull();
  });
});
