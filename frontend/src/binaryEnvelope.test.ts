import { describe, it, expect } from "vitest";

import { readBinaryEnvelope, formatBinaryLabel } from "./binaryEnvelope";
import { diffRuns } from "./diff";
import { buildAttempt, buildCaseResult, buildRun } from "./test-support";

const SHA = "a3f9" + "0".repeat(60);

const envelope = (sha256 = SHA, size = 10) => ({ $binary: { sha256, size } });

// Detection must be exact. A false positive replaces a user's real value with
// a "binary" chip, which is worse than showing a raw envelope object.
describe("readBinaryEnvelope", () => {
  it("matches the recorded fingerprint shape", () => {
    expect(readBinaryEnvelope(envelope())).toEqual({ sha256: SHA, size: 10 });
  });

  it("tolerates extra fields inside the envelope, so the format can grow", () => {
    expect(
      readBinaryEnvelope({
        $binary: { sha256: SHA, size: 10, mime: "image/png" },
      }),
    ).toEqual({ sha256: SHA, size: 10 });
  });

  it("refuses user data that merely carries a $binary key among others", () => {
    expect(
      readBinaryEnvelope({ $binary: { sha256: SHA, size: 1 }, note: "hi" }),
    ).toBe(null);
  });

  it("refuses wrong types inside the envelope", () => {
    expect(readBinaryEnvelope({ $binary: { sha256: 7, size: 1 } })).toBe(null);
    expect(readBinaryEnvelope({ $binary: { sha256: SHA, size: "1" } })).toBe(
      null,
    );
    expect(readBinaryEnvelope({ $binary: { sha256: SHA } })).toBe(null);
    expect(readBinaryEnvelope({ $binary: "a3f9" })).toBe(null);
    expect(readBinaryEnvelope({ $binary: null })).toBe(null);
    expect(readBinaryEnvelope({ $binary: [SHA, 1] })).toBe(null);
  });

  it("refuses non-envelope values outright", () => {
    expect(readBinaryEnvelope(null)).toBe(null);
    expect(readBinaryEnvelope(undefined)).toBe(null);
    expect(readBinaryEnvelope("$binary")).toBe(null);
    expect(readBinaryEnvelope([envelope()])).toBe(null);
    expect(readBinaryEnvelope({ data: envelope() })).toBe(null);
  });
});

describe("formatBinaryLabel", () => {
  it("names what is known: size and a short hash prefix", () => {
    expect(formatBinaryLabel({ sha256: SHA, size: 10 })).toBe(
      "binary · 10 B · a3f9…",
    );
    expect(formatBinaryLabel({ sha256: SHA, size: 1258291 })).toBe(
      "binary · 1.2 MB · a3f9…",
    );
  });
});

// The run diff compares values structurally, so two fingerprints are equal
// exactly when their full hashes are. Tested because hash equality is the one
// diff signal a content-free record offers.
describe("fingerprints in a run diff", () => {
  const runWithOutput = (id: string, output: unknown) =>
    buildRun(id, {
      t: {
        outcome: "passed",
        cases: {
          c: buildCaseResult({ attempts: [buildAttempt(true, { output })] }),
        },
      },
    });

  it("reads equal hashes as unchanged", () => {
    const diff = diffRuns(
      runWithOutput("a", envelope()),
      runWithOutput("b", envelope()),
    );
    const row = diff.tests[0]!.cases[0]!;
    expect(row.outputChanged).toBe(false);
    expect(row.changed).toBe(false);
  });

  it("reads different hashes as a change", () => {
    const diff = diffRuns(
      runWithOutput("a", envelope()),
      runWithOutput("b", envelope("ff01" + "0".repeat(60))),
    );
    const row = diff.tests[0]!.cases[0]!;
    expect(row.outputChanged).toBe(true);
    expect(row.changed).toBe(true);
  });

  it("reads a size change under the same hash as a change", () => {
    // Hash collisions aside, this only happens to corrupt data. It must not
    // show as "unchanged" just because the hash matched.
    const diff = diffRuns(
      runWithOutput("a", envelope(SHA, 10)),
      runWithOutput("b", envelope(SHA, 11)),
    );
    expect(diff.tests[0]!.cases[0]!.outputChanged).toBe(true);
  });
});
