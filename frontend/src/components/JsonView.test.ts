import { describe, it, expect } from "vitest";

import { diffCanCompare, rendersBlank } from "./JsonView";

// Strings are shown unquoted, which suits the prose most tasks produce. A
// string with nothing visible in it is the exception. Bare, it looks like a
// missing value rather than an empty one.
describe("rendersBlank", () => {
  it("is true for a string with nothing to show", () => {
    expect(rendersBlank("")).toBe(true);
    expect(rendersBlank("   ")).toBe(true);
    expect(rendersBlank("\n\t")).toBe(true);
  });

  it("is false as soon as a character would be visible", () => {
    expect(rendersBlank("UNKNOWN")).toBe(false);
    expect(rendersBlank(" a ")).toBe(false);
    expect(rendersBlank("0")).toBe(false);
  });
});

// A case can record a plain expected value while its task returns a richer
// object. Lining those up produces one whole-value replacement, which says
// less than the two values side by side.
describe("diffCanCompare", () => {
  it("compares two containers", () => {
    expect(diffCanCompare({ a: 1 }, { a: 2 })).toBe(true);
    expect(diffCanCompare([1], [2])).toBe(true);
  });

  it("compares two leaves", () => {
    expect(diffCanCompare("account", "billing")).toBe(true);
    expect(diffCanCompare(1, 2)).toBe(true);
    expect(diffCanCompare(null, "x")).toBe(true);
  });

  it("refuses a leaf against a container", () => {
    expect(diffCanCompare("account", { category: "account" })).toBe(false);
    expect(diffCanCompare({ category: "account" }, "account")).toBe(false);
  });

  it("judges the unwrapped value, as the diff itself does", () => {
    // A single-key result wrapper unwraps to its inner value, so the pair is
    // two leaves even though one side arrives as an object.
    expect(diffCanCompare("account", { output: "billing" })).toBe(true);
  });
});
