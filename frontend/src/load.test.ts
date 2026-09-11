import { describe, it, expect } from "vitest";

import { ApiError } from "./api";
import {
  loadInitial,
  loadRunExtras,
  resolveSlot,
  type ExtrasApi,
  type LoaderApi,
  type RunExtras,
} from "./App";
import type { RepositoryData } from "./components/Sidebar";
import type { Ref, ReflogEntry, RepositoryInfo, RunSummary } from "./types";

function ref(
  name: string,
  opts: { pr?: number; tipless?: boolean; error?: string } = {},
): Ref {
  const pr = opts.pr ?? null;
  const tip: ReflogEntry | null =
    opts.tipless || opts.error
      ? null
      : {
          run_id: `run-${name}`,
          moved_at: "2026-01-01T00:00:00Z",
          commit: "abc1234",
          pr,
          title: null,
        };
  return {
    name,
    tip,
    // The server classifies, and the fake listing mirrors what it sends.
    kind: name === "baseline" ? "baseline" : pr !== null ? "pr" : "other",
    error: opts.error ?? null,
  };
}

/** A loader API over a fixed ref listing, which records the endpoints it is
 *  asked for. It offers no per-ref endpoint, so a loader that still fetched each
 *  ref has nothing to call and fails the test. `refLogError` makes the history
 *  request fail with that error. */
function fakeApi(
  refs: Ref[],
  { refLogError }: { refLogError?: Error } = {},
): LoaderApi & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    refs: (slug) => {
      calls.push(`refs ${slug}`);
      return Promise.resolve(refs);
    },
    runs: (slug) => {
      calls.push(`runs ${slug}`);
      return Promise.resolve([]);
    },
    refLog: (slug, name) => {
      calls.push(`reflog ${slug} ${name}`);
      if (refLogError !== undefined) return Promise.reject(refLogError);
      return Promise.resolve([] as ReflogEntry[]);
    },
  };
}

const info: RepositoryInfo = {
  slug: "main",
  url: "./.evaltrack",
  role: "local",
};

describe("loadInitial", () => {
  it("loads every sidebar ref section from a single refs request", async () => {
    // The listing carries each ref's tip entry, so the sidebar needs no
    // follow-up per ref. That N+1 spent a storage read per ref on every
    // dashboard open.
    const api = fakeApi([
      ref("baseline", { pr: 7 }),
      ref("pr/2", { pr: 2 }),
      ref("pr/1", { pr: 1 }),
      ref("my-experiment"),
    ]);

    const loaded = await loadInitial(info, api);

    expect(api.calls).toEqual([
      "refs main",
      "runs main",
      "reflog main baseline",
    ]);
    expect(loaded.baseline?.tip?.run_id).toBe("run-baseline");
    expect(loaded.prs.map((r) => r.tip?.run_id)).toEqual([
      "run-pr/1",
      "run-pr/2",
    ]);
    expect(loaded.otherRefs.map((r) => r.name)).toEqual(["my-experiment"]);
  });

  it("groups and sorts by the server's kind, PR refs by number", async () => {
    // `baseline` keeps its own section even though its promote recorded a PR
    // number, and a PR-looking name without one is a plain ref.
    const api = fakeApi([
      ref("pr/10", { pr: 10 }),
      ref("baseline", { pr: 7 }),
      ref("pr/9", { pr: 9 }),
      ref("pr/no-number"),
      ref("alice/login-fix", { pr: 3 }),
    ]);

    const loaded = await loadInitial(info, api);

    expect(loaded.baseline?.name).toBe("baseline");
    expect(loaded.prs.map((r) => r.name)).toEqual([
      "alice/login-fix",
      "pr/9",
      "pr/10",
    ]);
    expect(loaded.otherRefs.map((r) => r.name)).toEqual(["pr/no-number"]);
  });

  it("keeps a ref that points at nothing in its section", async () => {
    // A ref creation that failed part way leaves a listed ref with no history.
    // It has to stay in the sidebar, because deleting it there is the only way
    // to remove it.
    const api = fakeApi([ref("pr/13", { tipless: true })]);

    const loaded = await loadInitial(info, api);

    expect(loaded.otherRefs.map((r) => r.name)).toEqual(["pr/13"]);
    expect(loaded.otherRefs[0]?.tip).toBeNull();
  });

  it("keeps a ref whose history cannot be read, with its error", async () => {
    // A torn reflog lists with no tip, like an empty one, and only the error
    // tells the sidebar which of the two it is showing.
    const torn = "corrupt reflog for ref 'baseline': line 2 is not readable";
    const api = fakeApi([ref("baseline", { error: torn })]);

    const loaded = await loadInitial(info, api);

    expect(loaded.baseline?.tip).toBeNull();
    expect(loaded.baseline?.error).toBe(torn);
  });

  it("reports a failed history request instead of showing an empty history", async () => {
    // Swallowing it left a torn `baseline` reflog looking like a repository
    // with no mainline. The refs and runs beside it still load, so the failure
    // is a notice rather than a failed repository.
    const api = fakeApi([ref("baseline"), ref("pr/1", { pr: 1 })], {
      refLogError: new ApiError(
        422,
        "cannot be read: corrupt reflog for ref 'baseline': line 2 is not readable",
      ),
    });
    const notices: string[] = [];

    const loaded = await loadInitial(info, api, (m) => notices.push(m));

    expect(notices).toEqual([
      "The history of baseline cannot be read: corrupt reflog for ref 'baseline': line 2 is not readable",
    ]);
    expect(loaded.baselineLog.items).toEqual([]);
    expect(loaded.prs.map((r) => r.name)).toEqual(["pr/1"]);
  });

  it("returns the whole ref listing, not a first page of it", async () => {
    // The listing arrives in one request, so there is nothing to page over.
    const api = fakeApi(
      Array.from({ length: 7 }, (_, i) => ref(`pr/${i + 1}`, { pr: i + 1 })),
    );

    const loaded = await loadInitial(info, api);

    expect(loaded.prs).toHaveLength(7);
    expect(api.calls).toEqual(["refs main", "runs main"]);
  });
});

/** A repository whose loaded listings name the given runs: its first page of
 *  runs, its baseline history, and the tips of its other refs. */
function repo(
  slug: string,
  listed: { runs?: string[]; baselineLog?: string[]; tips?: string[] } = {},
): RepositoryData {
  const section = <T>(items: T[]) => ({
    items,
    hasMore: false,
    loading: false,
  });
  const summary = (id: string): RunSummary => ({
    id,
    created_at: "2026-01-01T00:00:00Z",
    commit: null,
    worktree_dirty: null,
    labels: {},
    tests_total: 1,
    tests_failed: 0,
  });
  const entry = (id: string): ReflogEntry => ({
    run_id: id,
    moved_at: "2026-01-01T00:00:00Z",
    commit: null,
    pr: null,
    title: null,
  });
  return {
    slug,
    url: `./${slug}`,
    role: slug === "local" ? "local" : "remote",
    loading: false,
    baseline: null,
    prs: [],
    otherRefs: (listed.tips ?? []).map((id, i) => ({
      name: `ref-${i}`,
      tip: entry(id),
      kind: "other",
    })),
    baselineLog: section((listed.baselineLog ?? []).map(entry)),
    runs: section((listed.runs ?? []).map(summary)),
  };
}

const ID = "01M1R7G4HBN8XEZ3GZCJG1QZE0";

describe("resolveSlot", () => {
  it("trusts a qualified slot's slug", () => {
    // Nothing loaded lists the run. The fetch that follows is what finds out
    // whether the repository still has it. A probe here downloaded it twice.
    const res = resolveSlot([repo("local"), repo("remote")], {
      slug: "remote",
      runId: ID,
    });

    expect(res).toEqual({ repository: "remote", runId: ID });
  });

  it("matches a bare id against the loaded run page", () => {
    const res = resolveSlot([repo("local"), repo("remote", { runs: [ID] })], {
      slug: null,
      runId: ID,
    });

    expect(res).toEqual({ repository: "remote", runId: ID });
  });

  it("matches a bare id against the baseline history and the ref tips", () => {
    const repos = [repo("local")];
    expect(
      resolveSlot([...repos, repo("remote", { baselineLog: [ID] })], {
        slug: null,
        runId: ID,
      }),
    ).toEqual({ repository: "remote", runId: ID });
    expect(
      resolveSlot([...repos, repo("remote", { tips: [ID] })], {
        slug: null,
        runId: ID,
      }),
    ).toEqual({ repository: "remote", runId: ID });
  });

  it("hands an unlisted bare id to the local repository first, whatever the mount order", () => {
    const res = resolveSlot([repo("remote"), repo("local")], {
      slug: null,
      runId: ID,
    });

    expect(res).toEqual({ repository: "local", runId: ID });
  });

  it("treats a slug that is not mounted like a bare id", () => {
    const res = resolveSlot([repo("local"), repo("remote", { runs: [ID] })], {
      slug: "gone",
      runId: ID,
    });

    expect(res).toEqual({ repository: "remote", runId: ID });
  });

  it("resolves an empty slot to nothing", () => {
    expect(resolveSlot([repo("local")], null)).toBeNull();
  });

  it("resolves nothing when no repository is loaded", () => {
    expect(resolveSlot([], { slug: null, runId: ID })).toBeNull();
  });
});

/** An extras API over empty payloads, where an endpoint named in `failing`
 *  rejects the way an aborted request does. */
function fakeExtrasApi(failing: ("history" | "runMainline")[] = []): ExtrasApi {
  const fail = () => Promise.reject(new Error("Failed to fetch"));
  return {
    history: () =>
      failing.includes("history")
        ? fail()
        : Promise.resolve({ reliability: {}, score_history: {} }),
    runMainline: () =>
      failing.includes("runMainline") ? fail() : Promise.resolve(null),
  };
}

/** Run the loader over `client` and return what the run view would end up
 *  holding, plus every banner the reader was shown. */
async function loadExtras(client: ExtrasApi) {
  const patches: RunExtras[] = [];
  const notices: string[] = [];

  await loadRunExtras(
    { slug: "main", runId: ID },
    {
      client,
      store: (patch) => patches.push(patch),
      onNotice: (message) => notices.push(message),
    },
  );

  return { extras: Object.assign({}, ...patches) as RunExtras, notices };
}

describe("loadRunExtras", () => {
  it("reports a failed history request instead of a column and panels that just go", async () => {
    // Swallowing it hid the Reliability column and the trends, which is exactly
    // the view of a run with no mainline history behind it. The reader took the
    // run for one that had never been measured, while its header still named
    // the target.
    const { extras, notices } = await loadExtras(fakeExtrasApi(["history"]));

    expect(notices).toEqual([
      "This run's mainline history could not be loaded. The Reliability column and the score trends are hidden, not empty.",
    ]);
    expect(extras.history).toEqual({ status: "error" });
  });

  it("raises one banner for the one request behind both views", async () => {
    // The banner holds one message. While the column and the trends had a
    // request each, two failures meant the second silently replaced the first
    // and left the missing column unaccounted for.
    const { notices } = await loadExtras(
      fakeExtrasApi(["history", "runMainline"]),
    );

    expect(notices).toHaveLength(1);
  });

  it("stays quiet about a mainline entry that fails", async () => {
    // The entry only decorates the header with where the run was promoted.
    // Nothing about the run reads differently without it.
    const { extras, notices } = await loadExtras(
      fakeExtrasApi(["runMainline"]),
    );

    expect(notices).toEqual([]);
    expect(extras.mainline).toBeNull();
  });

  it("shows no banner when the extras load", async () => {
    const { extras, notices } = await loadExtras(fakeExtrasApi());

    expect(notices).toEqual([]);
    expect(extras.history).toEqual({
      status: "ready",
      data: { reliability: {}, score_history: {} },
    });
  });

  it("asks for the history once for a run whose views are already held", async () => {
    // The column and the trends come from one response, so a cached run needs
    // no request at all.
    let calls = 0;
    const client: ExtrasApi = {
      ...fakeExtrasApi(),
      history: () => {
        calls += 1;
        return Promise.resolve({ reliability: {}, score_history: {} });
      },
    };
    const patches: RunExtras[] = [];
    await loadRunExtras(
      { slug: "main", runId: ID },
      {
        client,
        cached: {
          history: {
            status: "ready",
            data: { reliability: {}, score_history: {} },
          },
          mainline: null,
        },
        store: (patch) => patches.push(patch),
      },
    );

    expect(calls).toBe(0);
    expect(patches).toEqual([]);
  });
});
