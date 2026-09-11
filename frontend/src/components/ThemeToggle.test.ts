import { describe, it, expect } from "vitest";

import { rememberTheme, resolveTheme } from "./ThemeToggle";
import type { ThemeStore } from "./ThemeToggle";

// What a browser set to block site data does. The object is there, and touching
// it raises. The theme is decided before React mounts, so a throw that escapes
// leaves a blank page instead of a dashboard.
const blocked: ThemeStore = {
  read: () => {
    throw new Error("The operation is insecure.");
  },
  write: () => {
    throw new Error("The operation is insecure.");
  },
};

function fakeStore(value: string | null = null): ThemeStore {
  let stored = value;
  return {
    read: () => stored,
    write: (v) => {
      stored = v;
    },
  };
}

describe("resolveTheme", () => {
  it("takes the remembered choice", () => {
    expect(resolveTheme(fakeStore("dark"))).toBe("dark");
  });

  it("falls back to the preferred theme when nothing sensible is stored", () => {
    const preferred = resolveTheme(fakeStore());
    expect(resolveTheme(fakeStore("chartreuse"))).toBe(preferred);
  });

  it("falls back to the preferred theme when the store throws", () => {
    expect(resolveTheme(blocked)).toBe(resolveTheme(fakeStore()));
  });
});

describe("rememberTheme", () => {
  it("stores the choice", () => {
    const store = fakeStore();
    rememberTheme("dark", store);
    expect(store.read()).toBe("dark");
  });

  it("survives a store that throws", () => {
    expect(() => rememberTheme("dark", blocked)).not.toThrow();
  });
});
