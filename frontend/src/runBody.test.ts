import { describe, it, expect } from "vitest";

import { awaitingRun, runBody, type RunFetch } from "./App";
import { buildRun } from "./test-support";
import type { Selection } from "./components/Sidebar";

function fetched(slug: string, runId: string): RunFetch {
  return { key: `${slug}:${runId}`, run: buildRun(runId, {}) };
}

function failed(slug: string, runId: string): RunFetch {
  return { key: `${slug}:${runId}`, run: null };
}

function selection(slug: string, runId: string): Selection {
  return { repository: slug, runId };
}

describe("runBody", () => {
  it("renders the body fetched for the current selection", () => {
    const body = runBody(
      fetched("local", "run-1"),
      selection("local", "run-1"),
    );

    expect(body?.id).toBe("run-1");
  });

  // The pane hands the body's verdict, cases and delete target the identity of
  // whatever is selected, so a body that outlives its selection mislabels the
  // one and misdirects the other.
  it("holds back a body the selection has moved on from", () => {
    expect(
      runBody(fetched("local", "run-1"), selection("local", "run-2")),
    ).toBeNull();
  });

  it("holds back a body from another repository's run of the same id", () => {
    expect(
      runBody(fetched("local", "run-1"), selection("remote", "run-1")),
    ).toBeNull();
  });

  it("has nothing to show with no selection", () => {
    expect(runBody(fetched("local", "run-1"), null)).toBeNull();
  });
});

describe("awaitingRun", () => {
  it("waits from the pick until that run's body lands", () => {
    expect(
      awaitingRun(fetched("local", "run-1"), selection("local", "run-2")),
    ).toBe(true);
    expect(
      awaitingRun(fetched("local", "run-2"), selection("local", "run-2")),
    ).toBe(false);
  });

  // A failed fetch is a result too. The banner says what went wrong, and the
  // pane must not spin for a body that is never coming.
  it("stops waiting once the fetch has failed", () => {
    expect(
      awaitingRun(failed("local", "run-1"), selection("local", "run-1")),
    ).toBe(false);
  });

  it("waits for nothing when no run is picked", () => {
    expect(awaitingRun(null, null)).toBe(false);
  });
});
