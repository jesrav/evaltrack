import { describe, it, expect } from "vitest";

import { resolveBaseline, type BaselineSource } from "./baseline";
import type { Ref, RepositoryRole } from "./types";

function baselineRef(runId: string | null): Ref {
  return {
    name: "baseline",
    kind: "baseline",
    tip:
      runId === null
        ? null
        : {
            run_id: runId,
            moved_at: "2026-01-01T00:00:00Z",
            commit: "abc1234",
            pr: null,
            title: null,
          },
    tip_run:
      runId === null
        ? null
        : {
            id: runId,
            created_at: "2026-01-01T00:00:00Z",
            commit: "abc1234",
            worktree_dirty: false,
            labels: {},
            tests_total: 1,
            tests_failed: 0,
          },
  };
}

function repo(
  slug: string,
  role: RepositoryRole,
  baselineRun: string | null = null,
): BaselineSource {
  return {
    slug,
    role,
    baseline: baselineRun === null ? null : baselineRef(baselineRun),
  };
}

describe("resolveBaseline", () => {
  it("takes the remote's baseline, never a local one beside it", () => {
    // A local baseline next to a remote is the developer's own promotion.
    const repos = [
      repo("local", "local", "run-l"),
      repo("main", "remote", "run-r"),
    ];

    expect(resolveBaseline(repos)).toEqual({
      repository: "main",
      runId: "run-r",
      via: "baseline",
    });
    expect(
      resolveBaseline([
        repo("local", "local", "run-l"),
        repo("main", "remote"),
      ]),
    ).toBeNull();
  });

  it("takes the sole repository's baseline when no remote is mounted", () => {
    const repos = [repo("local", "local", "run-l")];

    expect(resolveBaseline(repos)?.repository).toBe("local");
  });

  it("is null when the baseline tip points at nothing", () => {
    const repos: BaselineSource[] = [
      repo("local", "local"),
      { slug: "main", role: "remote", baseline: baselineRef(null) },
    ];

    expect(resolveBaseline(repos)).toBeNull();
  });

  it("is null when the baseline tip's run cannot be read", () => {
    // The tip still names a run, but the server found nothing to load for it.
    const repos: BaselineSource[] = [
      {
        slug: "main",
        role: "remote",
        baseline: { ...baselineRef("run-r"), tip_run: null },
      },
    ];

    expect(resolveBaseline(repos)).toBeNull();
  });
});
