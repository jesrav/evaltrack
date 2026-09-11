import { describe, it, expect } from "vitest";

import { ApiError } from "./api";
import {
  formatBytes,
  formatRatePct,
  formatReflogLoadMessage,
  formatRunLoadMessage,
  formatScore,
} from "./format";

describe("formatScore", () => {
  it("writes at most three decimals and drops trailing zeros", () => {
    expect(formatScore(0.12345)).toBe("0.123");
    expect(formatScore(0.5)).toBe("0.5");
    expect(formatScore(0.1 + 0.2)).toBe("0.3");
    expect(formatScore(0.0004)).toBe("0");
  });

  it("writes a whole number without a decimal point", () => {
    expect(formatScore(1)).toBe("1");
    expect(formatScore(0)).toBe("0");
    expect(formatScore(42)).toBe("42");
  });
});

describe("formatRatePct", () => {
  it("floors instead of rounding, so a rate never overstates itself", () => {
    // Rounding shows 199/200 as "100" and 1/250 as "0". Both lie about
    // whether the extreme was actually reached.
    expect(formatRatePct(1)).toBe("100");
    expect(formatRatePct(0.995)).toBe("99.5");
    expect(formatRatePct(199 / 200)).toBe("99.5");
    expect(formatRatePct(0.7)).toBe("70");
    expect(formatRatePct(1 / 250)).toBe("0.4");
    expect(formatRatePct(0)).toBe("0");
  });

  it("survives float noise in exact ratios (does not floor a boundary down)", () => {
    // 0.29 * 100 is 28.999… in floats. A naive floor shows "28".
    expect(formatRatePct(29 / 100)).toBe("29");
    expect(formatRatePct(7 / 10)).toBe("70");
  });

  it("reserves '100' for exactly 1 and '0' for exactly 0", () => {
    // The extremes are statements ("always passes" / "never passes"), so no
    // other value can render them. Sample across (0, 1) including the edges
    // where flooring is most tempted to collapse.
    const samples: number[] = [
      Number.EPSILON,
      1 - Number.EPSILON,
      1 / 10000,
      9999 / 10000,
      1 / 3,
      2 / 3,
    ];
    for (let i = 1; i < 500; i++) samples.push(i / 500);
    for (const rate of samples) {
      if (rate <= 0 || rate >= 1) continue;
      const label = formatRatePct(rate);
      expect(label).not.toBe("100");
      expect(label).not.toBe("0");
    }
  });
});

describe("formatBytes", () => {
  it("keeps small counts exact and larger ones compact", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(18)).toBe("18 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(10 * 1024)).toBe("10 KB");
    expect(formatBytes(1258291)).toBe("1.2 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3 GB");
  });

  it("shows a decimal only while it distinguishes anything", () => {
    // Above 10 units a tenth is noise. Below, "1.2 MB" vs "1.8 MB" is the
    // whole difference between a thumbnail and a photo.
    expect(formatBytes(Math.round(12.34 * 1024 ** 2))).toBe("12 MB");
    expect(formatBytes(Math.round(1.26 * 1024 ** 2))).toBe("1.3 MB");
  });
});

describe("formatRunLoadMessage", () => {
  it("explains a missing run instead of echoing the transport error", () => {
    // A stale deep-link to a deleted or garbage-collected run is the common way
    // to hit this, and the URL in the raw error tells the reader nothing.
    const msg = formatRunLoadMessage(
      new ApiError(404, "404 Not Found for /api/repositories/x/runs/ZZZ"),
      "ZZZ0123456789",
    );
    expect(msg).toBe(
      "Run ZZZ0123456 not found. It may have been deleted or cleaned up.",
    );
  });

  it("says a malformed id is not a run id, rather than echoing the 400", () => {
    // A hand-edited or truncated link sends an id the server rejects outright.
    // Nothing was deleted, so the 404 wording is wrong for it.
    const msg = formatRunLoadMessage(
      new ApiError(400, "400 Bad Request for /api/repositories/x/runs/nope"),
      "not-a-ulid",
    );
    expect(msg).toBe("Run not-a-ulid is not a valid run id.");
  });

  it("says a run that cannot be read is unreadable, not deleted", () => {
    // The run still sits in the list, so "deleted" sends the reader looking for
    // a cleanup that never happened. The server's detail says what is wrong and
    // what repairs it, and the banner keeps it.
    const msg = formatRunLoadMessage(
      new ApiError(
        422,
        "cannot be read: unreadable run ZZZ0123456789: runs/ZZZ0123456789.json does not parse as a stored run (bad JSON). Delete the run to reclaim the key.",
      ),
      "ZZZ0123456789",
    );
    expect(msg).toBe(
      "Run ZZZ0123456 cannot be read: unreadable run ZZZ0123456789: runs/ZZZ0123456789.json does not parse as a stored run (bad JSON). Delete the run to reclaim the key.",
    );
  });

  it("keeps the detail of anything that isn't a missing run", () => {
    // A 5xx or a network fault is not the user's stale link, and the detail is
    // the only thing to go on.
    expect(
      formatRunLoadMessage(new ApiError(503, "503 boom for /api/x"), "r1"),
    ).toBe("ApiError: 503 boom for /api/x");
    expect(formatRunLoadMessage(new TypeError("Failed to fetch"), "r1")).toBe(
      "TypeError: Failed to fetch",
    );
  });
});

describe("formatReflogLoadMessage", () => {
  it("names the ref whose history cannot be read and keeps the reason", () => {
    // A torn reflog takes the mainline views with it, and the server's detail
    // names the torn line and the repair.
    const msg = formatReflogLoadMessage(
      new ApiError(
        422,
        "cannot be read: corrupt reflog for ref 'baseline': line 2 of refs/baseline.log.jsonl is not a readable reflog entry (bad JSON).",
      ),
      "baseline",
    );
    expect(msg).toBe(
      "The history of baseline cannot be read: corrupt reflog for ref 'baseline': line 2 of refs/baseline.log.jsonl is not a readable reflog entry (bad JSON).",
    );
  });

  it("keeps the detail of any other failure", () => {
    expect(
      formatReflogLoadMessage(
        new ApiError(502, "storage backend error: token expired"),
        "baseline",
      ),
    ).toBe("ApiError: storage backend error: token expired");
  });
});
