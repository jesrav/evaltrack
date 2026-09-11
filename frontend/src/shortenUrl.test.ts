import { describe, it, expect } from "vitest";

import { shortenUrl } from "./shortenUrl";

// The sidebar prints a repository URL in a 280px column. A local repository under a
// temp path wraps over several lines and pushes the run list down the page.
describe("shortenUrl", () => {
  it("leaves a URL that already fits alone", () => {
    expect(shortenUrl("https://acme.blob.core.windows.net/evals", 60)).toBe(
      "https://acme.blob.core.windows.net/evals",
    );
  });

  it("drops middle segments and keeps both ends", () => {
    // The scheme says where the repository lives, the last segments say which one.
    expect(
      shortenUrl("/tmp/pytest-of-user/pytest-31/demo/.evaltrack", 30),
    ).toBe("/…/pytest-31/demo/.evaltrack");
  });

  it("keeps the host of a remote URL", () => {
    expect(
      shortenUrl("https://acme.blob.core.windows.net/evals/team/runs", 30),
    ).toBe("https://acme.blob.core.windows.net/…/runs");
  });

  it("keeps the last segment however long it is", () => {
    // Cutting into it drops the one part that identifies the repository.
    expect(shortenUrl("/tmp/a-very-long-directory-name", 20)).toBe(
      "/…/a-very-long-directory-name",
    );
  });

  it("adds no gap when there is nothing to drop", () => {
    expect(shortenUrl("/a-single-long-segment", 10)).toBe(
      "/a-single-long-segment",
    );
    expect(shortenUrl("https://a-very-long-host-name-with-no-path", 10)).toBe(
      "https://a-very-long-host-name-with-no-path",
    );
  });

  it("shortens a plain path with no scheme", () => {
    expect(shortenUrl("/home/user/projects/support-bot/.evaltrack", 30)).toBe(
      "/…/support-bot/.evaltrack",
    );
  });
});
