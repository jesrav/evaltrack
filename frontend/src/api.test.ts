import { describe, it, expect } from "vitest";

import { readJsonWithProgress, type Progress } from "./api";

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
