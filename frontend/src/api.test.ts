import { describe, it, expect } from "vitest";

import { api, readJsonWithProgress, type Progress } from "./api";

function chunkedResponse(
  chunks: string[],
  headers: Record<string, string> = {},
): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, { headers });
}

describe("readJsonWithProgress", () => {
  it("reports the bytes arrived against the Content-Length, then parses", async () => {
    const seen: Progress[] = [];
    const res = chunkedResponse(['{"a": ', "[1, 2]}"], {
      "content-length": "13",
    });
    const data = await readJsonWithProgress<{ a: number[] }>(res, (p) =>
      seen.push(p),
    );
    expect(data).toEqual({ a: [1, 2] });
    expect(seen).toEqual([
      { loaded: 6, total: 13 },
      { loaded: 13, total: 13 },
    ]);
  });

  it("reports a null total when the server sent no length", async () => {
    const seen: Progress[] = [];
    const data = await readJsonWithProgress<number>(
      chunkedResponse(["42"]),
      (p) => seen.push(p),
    );
    expect(data).toBe(42);
    expect(seen).toEqual([{ loaded: 2, total: null }]);
  });
});

describe("runReportUrl", () => {
  it("names the labels and the comparison in the query", () => {
    const url = api.runReportUrl("local", "01B", {
      via: "pr/7",
      against: { slug: "remote", id: "01A", via: "baseline" },
    });

    expect(url).toBe(
      "/api/repositories/local/runs/01B/report?via=pr%2F7&against=01A&against_slug=remote&against_via=baseline",
    );
  });

  it("carries no query for a run alone", () => {
    expect(api.runReportUrl("local", "01B")).toBe(
      "/api/repositories/local/runs/01B/report",
    );
  });

  it("keeps the query in a browser without URLSearchParams.size", () => {
    // Safari before 17 and Chromium before 113 have no `size`, and a page in
    // one of them downloaded a report of the wrong run.
    const proto = URLSearchParams.prototype as { size?: number };
    const descriptor = Object.getOwnPropertyDescriptor(proto, "size");
    Reflect.deleteProperty(proto, "size");
    try {
      const url = api.runReportUrl("local", "01B", {
        against: { slug: "remote", id: "01A" },
      });
      expect(url).toContain("?against=01A&against_slug=remote");
    } finally {
      if (descriptor) Object.defineProperty(proto, "size", descriptor);
    }
  });
});
