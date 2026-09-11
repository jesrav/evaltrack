import { describe, it, expect } from "vitest";

import {
  getPrimaryView,
  formatCanonicalText,
  formatDisplayText,
  truncate,
} from "./extract";

describe("getPrimaryView", () => {
  it("unwraps a single recognised result key, ignoring _-prefixed metadata", () => {
    expect(
      getPrimaryView({ output: "Hi", _state: { big: 1 }, _trace: "x" }),
    ).toBe("Hi");
  });

  it("recurses through nested wrappers", () => {
    expect(getPrimaryView({ output: { result: "deep" } })).toBe("deep");
  });

  it("leaves structured outputs with multiple public keys unchanged", () => {
    const value = { answer: "yes", confidence: 0.9 };
    expect(getPrimaryView(value)).toBe(value);
  });

  it("leaves arrays and primitives unchanged", () => {
    expect(getPrimaryView([1, 2, 3])).toEqual([1, 2, 3]);
    expect(getPrimaryView("plain")).toBe("plain");
    expect(getPrimaryView(null)).toBe(null);
  });
});

describe("formatCanonicalText", () => {
  it("is key-order independent for objects", () => {
    expect(formatCanonicalText({ a: 1, b: 2 })).toBe(
      formatCanonicalText({ b: 2, a: 1 }),
    );
  });

  it("returns strings unwrapped", () => {
    expect(formatCanonicalText({ output: "answer" })).toBe("answer");
  });

  it("distinguishes different values", () => {
    expect(formatCanonicalText({ output: "a" })).not.toBe(
      formatCanonicalText({ output: "b" }),
    );
  });
});

describe("formatDisplayText / truncate", () => {
  it("renders nullish as an em dash", () => {
    expect(formatDisplayText(null)).toBe("—");
    expect(formatDisplayText(undefined)).toBe("—");
  });

  it("truncates only when longer than max", () => {
    expect(truncate("short", 10)).toBe("short");
    expect(truncate("abcdef", 4)).toBe("abc…");
  });
});
