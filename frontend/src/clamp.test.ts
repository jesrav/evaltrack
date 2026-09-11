import { describe, it, expect } from "vitest";

import { isClamped } from "./clamp";

describe("isClamped", () => {
  it("is false when the content fits exactly", () => {
    expect(isClamped(100, 100)).toBe(false);
  });

  it("treats a 1px overflow as sub-pixel rounding, not real truncation", () => {
    // -webkit-line-clamp can round scrollHeight up by a pixel with no actual
    // cut. That must not flip a short docstring into pressable.
    expect(isClamped(101, 100)).toBe(false);
  });

  it("is true once content genuinely overflows the clamp", () => {
    expect(isClamped(102, 100)).toBe(true);
  });
});
