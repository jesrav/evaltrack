import { describe, it, expect } from "vitest";

import {
  caseKeyOf,
  collectDeferred,
  readDeferredEnvelope,
  resolveDeferred,
  type DeferredEnvelope,
} from "./deferred";
import type { CaseRecord } from "./types";

function envelope(over: Partial<DeferredEnvelope> = {}): {
  $deferred: DeferredEnvelope;
} {
  return {
    $deferred: {
      preview: "the ans",
      size: 700_000,
      sha256: "ab".repeat(32),
      run: "01RUN",
      test: "tests/test_x.py::test_a",
      case: "c1",
      field: "output",
      attempt: 0,
      ...over,
    },
  };
}

function caseRecord(over: Partial<CaseRecord> = {}): CaseRecord {
  return {
    inputs: "the input",
    expected_output: null,
    metadata: null,
    attempts: [
      {
        outcome: "passed",
        output: "the answer, whole",
        results: {},
        task_duration: 1,
      },
    ],
    passed_attempts: 1,
    clean_attempts: 1,
    errored_attempts: 0,
    outcome: "passed",
    ...over,
  };
}

describe("readDeferredEnvelope", () => {
  it("reads the server's envelope", () => {
    const env = readDeferredEnvelope(envelope());
    expect(env?.preview).toBe("the ans");
    expect(env?.attempt).toBe(0);
  });

  it("leaves a case's own field without an attempt", () => {
    const env = readDeferredEnvelope(
      envelope({ field: "inputs", attempt: undefined }),
    );
    expect(env?.field).toBe("inputs");
    expect(env?.attempt).toBeUndefined();
  });

  it("refuses user data that only resembles one", () => {
    expect(readDeferredEnvelope({ $deferred: {}, other: 1 })).toBeNull();
    expect(readDeferredEnvelope({ $deferred: { preview: "x" } })).toBeNull();
    expect(readDeferredEnvelope("text")).toBeNull();
  });
});

describe("collectDeferred", () => {
  it("finds envelopes anywhere in a drawer's content, one per value", () => {
    const content = {
      kind: "case",
      caseResult: caseRecord({
        inputs: envelope({ field: "inputs", attempt: undefined }),
        attempts: [
          {
            outcome: "passed",
            output: envelope(),
            results: {},
            task_duration: 1,
          },
          {
            outcome: "passed",
            output: envelope({ attempt: 1 }),
            results: {},
            task_duration: 1,
          },
        ],
      }),
    };
    const found = collectDeferred(content);
    expect(found.map((e) => `${e.field}:${e.attempt ?? ""}`)).toEqual([
      "inputs:",
      "output:0",
      "output:1",
    ]);
  });

  it("finds nothing in plain values", () => {
    expect(collectDeferred({ a: [1, { b: "c" }] })).toEqual([]);
  });
});

describe("resolveDeferred", () => {
  it("swaps each envelope for the value in its fetched case", () => {
    const content = {
      a: envelope(),
      b: envelope({ field: "inputs", attempt: undefined }),
      untouched: { x: 1 },
    };
    const cases = new Map([[caseKeyOf(envelope().$deferred), caseRecord()]]);
    const resolved = resolveDeferred(content, cases);
    expect(resolved.a).toBe("the answer, whole");
    expect(resolved.b).toBe("the input");
    expect(resolved.untouched).toBe(content.untouched);
  });

  it("keeps an envelope whose case did not arrive", () => {
    const content = { a: envelope() };
    expect(resolveDeferred(content, new Map())).toBe(content);
  });
});
