import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type Progress } from "./api";
import { createDrawerOpener } from "./drawerOpener";
import {
  formatBytes,
  formatReflogLoadMessage,
  formatRunLoadMessage,
} from "./format";
import {
  Sidebar,
  repositoryTitle,
  type PaginatedSection,
  type RepositoryData,
  type SectionKey,
  type Selection,
} from "./components/Sidebar";
import { resolveBaseline } from "./baseline";
import { RunDetail } from "./components/RunDetail";
import { RunDiff } from "./components/RunDiff";
import { ConfirmDialog, type ConfirmRequest } from "./components/ConfirmDialog";
import { Drawer, type DrawerContent } from "./components/Drawer";
import type {
  RunRecord,
  HistoryState,
  MainlineEntry,
  Ref,
  ReflogEntry,
  RepositoryInfo,
  RunSummary,
} from "./types";

/** How many items to fetch per "load more" click across every paginated
 *  sidebar section. Small, so a long suite doesn't flood the sidebar. */
const PAGE_SIZE = 5;

function selectionEquals(a: Selection | null, b: Selection | null): boolean {
  if (a === null || b === null) return a === b;
  return a.repository === b.repository && a.runId === b.runId;
}

/** Cache key for `extrasByRun` and the run fetches. A run id can live in more
 *  than one mounted repository and its cross-run history is measured against
 *  the mainline of the repository it was opened from, so the slug must be part
 *  of the key. */
function runKey(slug: string, runId: string): string {
  return `${slug}:${runId}`;
}

/** What the run view fetches for an open run beyond the run itself. Each field
 *  lands from its own request, so one is ready while another still loads.
 *  `mainline` is absent until its request completes, then null for a run that
 *  was never promoted. */
export interface RunExtras {
  history?: HistoryState;
  mainline?: MainlineEntry | null;
}

interface RunSlot {
  slug: string | null;
  runId: string;
}

/** How a slot's run fetch ended, tagged with the selection it was made for.
 *  A null `run` is a failed fetch. The notice banner carries the reason. */
export interface RunFetch {
  key: string;
  run: RunRecord | null;
}

/** The body a slot renders. A payload belongs only to the selection it was
 *  fetched for. Rendering the one still in hand under a newly picked run puts
 *  the old verdict, stats and cases under the new run's identity, and aims its
 *  download and its delete at a run the user is no longer looking at. So a slot
 *  has nothing to show between a pick and its payload landing. */
export function runBody(
  fetched: RunFetch | null,
  sel: Selection | null,
): RunRecord | null {
  if (!fetched || !sel) return null;
  return fetched.key === runKey(sel.repository, sel.runId) ? fetched.run : null;
}

/** The loading message for a run. It shows the bytes that arrived, and the
 *  total when the server sent one. */
export function formatRunProgress(progress: Progress | null): string {
  if (!progress || progress.loaded === 0) return "Loading run…";
  const loaded = formatBytes(progress.loaded);
  return progress.total
    ? `Loading run… ${loaded} of ${formatBytes(progress.total)}`
    : `Loading run… ${loaded}`;
}

/** Whether a slot is picked and its body still on the way, which is the one
 *  state that is neither a run view nor an invitation to pick one. */
export function awaitingRun(
  fetched: RunFetch | null,
  sel: Selection | null,
): boolean {
  if (!sel) return false;
  return fetched?.key !== runKey(sel.repository, sel.runId);
}

/** A destructive action waiting on its confirm dialog, with what to ask and
 *  what to do when the answer is yes. */
interface PendingConfirm extends ConfirmRequest {
  run: () => void;
}

/** Which repository a delete acts on, for the confirm dialog. A delete in the
 *  Remote section removes from the shared repository rather than from a working
 *  copy. So the dialog names the repository instead of relying on the section
 *  the user clicked in. */
function describeRepository(repos: RepositoryData[], slug: string): string {
  const repo = repos.find((r) => r.slug === slug);
  if (!repo) return `In repository: ${slug}`;
  return `In repository: ${repositoryTitle(repo)} (${repo.url})`;
}

/** A delete the server refused because a ref still reaches the run (409).
 *  The message names the refs, so it can give the fix as well, rather than
 *  showing a raw error. */
function deleteErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    return e.status === 409
      ? `${e.message}. Delete that ref instead: it cascades to the runs only it reaches.`
      : e.message;
  }
  return String(e);
}

/** Parse a `?run=` / `?compare=` value into an optional repo slug + run id.
 *  Accepts `slug:runId` (qualified) or a bare `runId` (resolved by search). */
function parseSlot(value: string | null): RunSlot | null {
  if (!value) return null;
  const i = value.indexOf(":");
  if (i < 0) return { slug: null, runId: value };
  return { slug: value.slice(0, i), runId: value.slice(i + 1) };
}

/** Both mean "no repository will ever serve this id", so the link is dead
 *  rather than the server unwell. */
function isDeadStatus(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 400);
}

/** Drop a `?run=` / `?compare=` parameter that names a run the view is not
 *  showing, so the URL stops claiming a selection it does not have. */
function dropUrlParam(name: "run" | "compare"): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has(name)) return;
  url.searchParams.delete(name);
  window.history.replaceState(null, "", url);
}

/** Local before remote, mount order within a role. A bare run id most often
 *  comes from the link the CLI printed for the run it just recorded, which is
 *  a local one. */
function byRole(repos: RepositoryData[]): RepositoryData[] {
  const rank = (r: RepositoryData) => (r.role === "local" ? 0 : 1);
  return [...repos].sort((x, y) => rank(x) - rank(y));
}

/** Whether a repository's loaded listings name the run: its run page, its
 *  baseline history, or a ref tip. */
function listsRun(repo: RepositoryData, runId: string): boolean {
  return (
    repo.runs.items.some((r) => r.id === runId) ||
    repo.baselineLog.items.some((e) => e.run_id === runId) ||
    [repo.baseline, ...repo.prs, ...repo.otherRefs].some(
      (r) => r?.tip?.run_id === runId,
    )
  );
}

/** Resolve a slot to a Selection from loaded state alone, without a request.
 *  Whether the run is still there is for the run fetch to find out, and its
 *  failure is what reports a dead link. Probing here downloaded every linked
 *  run twice.
 *
 *  A qualified slot names its repository. A bare id, or one under a slug that
 *  is not mounted, goes to the repository whose loaded listings name it, else
 *  to the first repository in role order. Null only when no repository is
 *  loaded, so there is nowhere to fetch from. */
export function resolveSlot(
  repos: RepositoryData[],
  slot: RunSlot | null,
): Selection | null {
  if (!slot) return null;
  if (slot.slug !== null && repos.some((r) => r.slug === slot.slug)) {
    return { repository: slot.slug, runId: slot.runId };
  }
  const ordered = byRole(repos);
  const repo = ordered.find((r) => listsRun(r, slot.runId)) ?? ordered[0];
  return repo ? { repository: repo.slug, runId: slot.runId } : null;
}

/** The opening view when the URL specifies nothing. The latest single run,
 *  which is the latest local run if a local repo is mounted, else the latest
 *  remote or baseline run. Opening on one run keeps the first view legible.
 *  Comparing against the mainline is an explicit action in the run header. */
function defaultSelection(repos: RepositoryData[]): Selection | null {
  const local = repos.find((r) => r.role === "local") ?? null;
  const localLatest = local?.runs.items[0] ?? null; // runs are newest-first

  const localSel: Selection | null =
    local && localLatest
      ? { repository: local.slug, runId: localLatest.id }
      : null;
  const baselineSel = resolveBaseline(repos);

  return localSel ?? baselineSel;
}

// ---- repository loaders -------------------------------------------------

interface GroupedRefs {
  baseline: Ref | null;
  prs: Ref[];
  other: Ref[];
}

/** Group server-classified refs into the sidebar's sections by their `kind`.
 *  No name parsing here. `baseline` is the one reserved name, and PR refs are
 *  those the server classified as carrying a stored PR number. */
function groupRefs(refs: Ref[]): GroupedRefs {
  const baseline = refs.find((r) => r.kind === "baseline") ?? null;
  const prs = refs
    .filter((r) => r.kind === "pr")
    .sort((a, b) => (a.tip?.pr ?? Infinity) - (b.tip?.pr ?? Infinity));
  const other = refs
    .filter((r) => r.kind === "other")
    .sort((a, b) => a.name.localeCompare(b.name));
  return { baseline, prs, other };
}

/** The endpoints a repository's first load reads, injectable so a test can count
 *  the requests it makes. */
export interface LoaderApi {
  refs: typeof api.refs;
  runs: typeof api.runs;
  refLog: typeof api.refLog;
}

/** Load a repository's sidebar data. The mainline history is the one request
 *  whose failure does not fail the load, because the runs and refs beside it
 *  still render. It goes to `onNotice` instead, with the server's reason, since a
 *  torn `baseline` reflog also takes the reliability and score history with it
 *  and a history that is silently empty reads as a repository without one. */
export async function loadInitial(
  { slug, url, role }: RepositoryInfo,
  client: LoaderApi = api,
  onNotice: (message: string) => void = () => {},
): Promise<RepositoryData> {
  // One request for the whole ref listing. It carries each ref's tip entry, so
  // the sidebar needs no per-ref follow-up.
  const [allRefs, runs] = await Promise.all([
    client.refs(slug),
    client.runs(slug, PAGE_SIZE, 0),
  ]);
  const grouped = groupRefs(allRefs);
  const baseline = grouped.baseline;

  const baselineLog = baseline
    ? await client
        .refLog(slug, baseline.name, PAGE_SIZE, 0)
        .catch((e: unknown): ReflogEntry[] => {
          onNotice(formatReflogLoadMessage(e, baseline.name));
          return [];
        })
    : [];

  return {
    slug,
    url,
    role,
    loading: false,
    baseline,
    prs: grouped.prs,
    otherRefs: grouped.other,
    baselineLog: makeSection(baselineLog, baselineLog.length === PAGE_SIZE),
    runs: makeSection(runs, runs.length === PAGE_SIZE),
  };
}

/** A repository entry shown before its data has loaded, with empty sections and
 *  `loading: true`, so the sidebar renders the block with a spinner
 *  immediately. A fast local repo replaces this almost at once. A slow remote
 *  keeps spinning while the rest of the UI stays interactive. */
function placeholderRepo({ slug, url, role }: RepositoryInfo): RepositoryData {
  const empty = <T,>(): PaginatedSection<T> => ({
    items: [],
    hasMore: false,
    loading: false,
  });
  return {
    slug,
    url,
    role,
    loading: true,
    baseline: null,
    prs: [],
    otherRefs: [],
    baselineLog: empty(),
    runs: empty(),
  };
}

function makeSection<T>(items: T[], hasMore: boolean): PaginatedSection<T> {
  return { items, hasMore, loading: false };
}

/** Apply a ref deletion to a repository's loaded state in place. Drop the ref
 *  from its section and remove the cascaded runs from the loaded run window.
 *  Pagination is preserved, so nothing refetches. */
function applyRefDeleted(
  repo: RepositoryData,
  name: string,
  deletedRuns: Set<string>,
): RepositoryData {
  const dropRef = (refs: Ref[]): Ref[] => refs.filter((x) => x.name !== name);
  return {
    ...repo,
    baseline: repo.baseline?.name === name ? null : repo.baseline,
    prs: dropRef(repo.prs),
    otherRefs: dropRef(repo.otherRefs),
    runs: {
      ...repo.runs,
      items: repo.runs.items.filter((x) => !deletedRuns.has(x.id)),
    },
  };
}

/** Banner text for a cross-run history that did not load. It names what the
 *  view is missing, because a missing column and missing trend panels are
 *  exactly how a run with no mainline history behind it renders: hidden, not
 *  empty. Left silent, a request that did not land reads as a fact about the
 *  eval. */
const HISTORY_LOAD_MESSAGE =
  "This run's mainline history could not be loaded. The Reliability column and the score trends are hidden, not empty.";

/** The endpoints an open run's extras come from, injectable so a test can make
 *  one of them fail. */
export interface ExtrasApi {
  history: typeof api.history;
  runMainline: typeof api.runMainline;
}

/** Load the extras for an open run, storing each as it lands. Two separate
 *  requests, so a slow mainline entry never delays the case table, and the
 *  history is stored `loading` first so the view can show a spinner. An entry
 *  already `ready` in `cached` is not fetched again.
 *
 *  A failed history request goes to `onNotice` as well as to the entry's
 *  `error`. The mainline entry only decorates the header, so it stays best
 *  effort and quiet: on failure it stores null and the note does not show, and
 *  a stored null is not fetched again. */
export async function loadRunExtras(
  { slug, runId }: { slug: string; runId: string },
  {
    client = api,
    cached = {},
    store,
    onNotice = () => {},
  }: {
    client?: ExtrasApi;
    cached?: RunExtras;
    store: (patch: RunExtras) => void;
    onNotice?: (message: string) => void;
  },
): Promise<void> {
  const pending: Promise<void>[] = [];
  if (cached.history?.status !== "ready") {
    store({ history: { status: "loading" } });
    pending.push(
      client
        .history(slug, runId)
        .then((data) => store({ history: { status: "ready", data } }))
        .catch(() => {
          onNotice(HISTORY_LOAD_MESSAGE);
          store({ history: { status: "error" } });
        }),
    );
  }
  if (cached.mainline === undefined) {
    pending.push(
      client
        .runMainline(slug, runId)
        .then((mainline) => store({ mainline }))
        .catch(() => store({ mainline: null })),
    );
  }
  await Promise.all(pending);
}

// ---- app component ------------------------------------------------------

export function App() {
  const [repositories, setRepositories] = useState<RepositoryData[]>([]);
  // Latest-state mirror for the handlers that read a section's length to
  // compute the next page offset. A click can land between a state update and
  // the next render, so reading a ref rather than the render closure keeps the
  // offset correct and no page is refetched or skipped.
  const repositoriesRef = useRef<RepositoryData[]>([]);
  const [selA, setSelA] = useState<Selection | null>(null);
  const [selB, setSelB] = useState<Selection | null>(null);
  const [fetchedA, setFetchedA] = useState<RunFetch | null>(null);
  const [fetchedB, setFetchedB] = useState<RunFetch | null>(null);
  const runA = runBody(fetchedA, selA);
  const runB = runBody(fetchedB, selB);
  // The run view's extras per open run, fetched lazily and cached. Keyed by
  // `slug:runId`, see `runKey`.
  const [extrasByRun, setExtrasByRun] = useState<Record<string, RunExtras>>({});
  // Mirror of `extrasByRun` so the fetch effect can read the current state
  // synchronously (to skip a cached result) without listing the map as a
  // dependency, which otherwise refetches on every change.
  const extrasByRunRef = useRef<Record<string, RunExtras>>({});
  // Project-level `{pr}` URL template (from `[tool.evaltrack].pr_url_template`),
  // fetched once. Null until loaded or when unset. PR numbers then render as
  // plain text rather than links.
  const [prUrlTemplate, setPrUrlTemplate] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerContent | null>(null);
  // The bytes of the open run that arrived so far, shown while it loads.
  const [runProgress, setRunProgress] = useState<Progress | null>(null);
  // The pending destructive action, with what the dialog asks and what to run
  // if the answer is yes. Null when no dialog is open.
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const cancelConfirm = useCallback(() => setConfirm(null), []);
  // `error` is reserved for the fatal case, where the repository list itself failed,
  // so there is nothing to render and it takes over the pane. `notice` is a
  // dismissible banner for recoverable, action-scoped failures (one repo failing
  // to load, a run fetch, a load-more, a delete), and the run view stays
  // visible.
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const dismissNotice = useCallback(() => setNotice(null), []);
  const [drawerOpener] = useState(() =>
    createDrawerOpener({ show: setDrawer, onError: setNotice }),
  );
  const closeDrawer = useCallback(() => drawerOpener.close(), [drawerOpener]);
  // Which attempt is selected per case, keyed by the case's title. Shared so
  // selecting an attempt in the inspector updates the run-view row. Unset cases
  // fall back to the deciding attempt. Ephemeral, not deep-linked.
  const [attemptSel, setAttemptSel] = useState<Record<string, number>>({});
  const selectAttempt = useCallback((key: string, index: number) => {
    setAttemptSel((m) => ({ ...m, [key]: index }));
  }, []);
  // The key carries no run id, so a pick made here follows the user into the
  // next run opened and sets an unrelated case to attempt 3. A pick belongs to
  // the runs it was made against, so drop it when they change.
  const slotKeys = [selA, selB]
    .map((s) => (s ? runKey(s.repository, s.runId) : ""))
    .join("|");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- resetting on a selection change, not state derivable during render.
    setAttemptSel((m) => (Object.keys(m).length === 0 ? m : {}));
  }, [slotKeys]);

  // The query string as it was on first load, captured before the URL-sync
  // effect can rewrite it. Used to restore a selection from a shared/CLI link.
  const initialSearch = useMemo(() => window.location.search, []);

  // True once the selection reflects an explicit choice (a click or a URL/CLI
  // link) rather than the auto-computed default. Only explicit selections are
  // written back to the URL, so a fresh `evaltrack ui` always recomputes
  // "latest local vs baseline" instead of keeping a stale comparison.
  const selectionIsExplicit = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const repos = await api.repositories();
        if (cancelled) return;
        // Paint every repository immediately as a spinner placeholder, then load
        // each one independently and swap in its data the moment it arrives. A
        // fast local repo shows its runs right away instead of waiting on a slow
        // remote (e.g. Azure), which keeps spinning in place.
        setRepositories(repos.map(placeholderRepo));

        // A `?run=`/`?compare=` link owns the selection. When neither is present
        // the default is picked the moment the relevant repo loads, rather than
        // after every repo (see the local fast path below and the fallback after
        // all loads finish).
        const params = new URLSearchParams(initialSearch);
        const hasUrlSelection =
          params.get("run") !== null || params.get("compare") !== null;

        const results = await Promise.all(
          repos.map((info) =>
            loadInitial(info, api, (message) => {
              if (!cancelled) setNotice(message);
            })
              .then((data) => {
                if (!cancelled) {
                  setRepositories((rs) =>
                    rs.map((r) => (r.slug === data.slug ? data : r)),
                  );
                  // Fast path for the default. Select the newest local run as
                  // soon as the local repo is ready, so opening the UI does not
                  // wait on a slow remote. `prev ?? …` leaves any selection already
                  // made (a click, or this firing twice) untouched.
                  const latest = data.runs.items[0];
                  if (
                    !hasUrlSelection &&
                    !selectionIsExplicit.current &&
                    data.role === "local" &&
                    latest !== undefined
                  ) {
                    setSelA(
                      (prev) =>
                        prev ?? { repository: data.slug, runId: latest.id },
                    );
                  }
                }
                return data;
              })
              .catch((e: unknown) => {
                // One repo failing (e.g. expired Azure creds) must not blank
                // the others. Clear its spinner and surface the error, but keep
                // the rest of the dashboard alive.
                if (!cancelled) {
                  setNotice(String(e));
                  setRepositories((rs) =>
                    rs.map((r) =>
                      r.slug === info.slug ? { ...r, loading: false } : r,
                    ),
                  );
                }
                return null;
              }),
          ),
        );
        if (cancelled) return;
        const loaded = results.filter((r): r is RepositoryData => r !== null);

        // Restore selection from the URL (?run=..., ?compare=...), or fall back
        // to the default single-run view when it specifies nothing. This
        // covers the cases the local fast path above cannot: no local repo, an
        // empty local repo, or a baseline-only default. `prev ?? …` preserves a
        // selection the fast path (or a click) already made.
        const runSlot = parseSlot(params.get("run"));
        const compareSlot = parseSlot(params.get("compare"));
        const runSel = resolveSlot(loaded, runSlot);
        const compareSel = resolveSlot(loaded, compareSlot);
        // A link with no repository to resolve against never reaches the run
        // fetch that would otherwise say why it shows nothing. A silent fall
        // back to the default view makes a dead link look like a live one.
        const unresolved = (slot: RunSlot | null, name: "run" | "compare") => {
          if (!slot) return;
          const notFound = new ApiError(404, `run not found: ${slot.runId}`);
          setNotice(formatRunLoadMessage(notFound, slot.runId));
          dropUrlParam(name);
        };
        if (!runSel) unresolved(runSlot, "run");
        if (!compareSel) unresolved(compareSlot, "compare");
        if (runSel || compareSel) {
          selectionIsExplicit.current = true;
          if (runSel) setSelA(runSel);
          if (compareSel) setSelB(compareSel);
        } else {
          const def = defaultSelection(loaded);
          if (def) setSelA((prev) => prev ?? def);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // Intentional run-once initial load. `initialSearch` is the only outer
    // value the effect reads that the linter flags, and it is a first-render
    // constant (useMemo with [] deps), so listing it never re-fires the
    // effect. The empty array keeps the mount-only semantics explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch the full run payload for each selected slot. Two slots → two
  // independent effects so picking B doesn't refetch A. A `cancelled` flag
  // guards against the race where a rapid selection switch makes a stale
  // fetch's `.then(setRunA)` land after the newer fetch and clobber it.
  //
  // A run no repository serves (deleted, cleaned up, or an id that never was
  // one) leaves the slot with nothing to show, so the slot falls back: A to
  // the default view, B to no comparison. The parameter that named the run is
  // dropped from the URL, so it stops claiming a selection the view isn't
  // showing. A fallback to the default is not an explicit pick, so it is not
  // written back to the URL either.
  useEffect(() => {
    if (!selA) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clearing the stale payload when the selection empties is part of this fetch effect's contract, not state derivable during render.
      setFetchedA(null);
      return;
    }
    let cancelled = false;
    const key = runKey(selA.repository, selA.runId);
    setRunProgress(null);
    api
      .run(selA.repository, selA.runId, (progress) => {
        if (!cancelled) setRunProgress(progress);
      })
      .then((run) => {
        if (!cancelled) setFetchedA({ key, run });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setNotice(formatRunLoadMessage(e, selA.runId));
        setFetchedA({ key, run: null });
        if (isDeadStatus(e)) {
          dropUrlParam("run");
          selectionIsExplicit.current = false;
          const def = defaultSelection(repositoriesRef.current);
          // The default itself failing must not refetch it forever.
          setSelA(def && !selectionEquals(def, selA) ? def : null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selA]);

  useEffect(() => {
    if (!selB) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- same contract as the selA effect above.
      setFetchedB(null);
      return;
    }
    let cancelled = false;
    const key = runKey(selB.repository, selB.runId);
    api
      .run(selB.repository, selB.runId)
      .then((run) => {
        if (!cancelled) setFetchedB({ key, run });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setNotice(formatRunLoadMessage(e, selB.runId));
        setFetchedB({ key, run: null });
        if (isDeadStatus(e)) {
          dropUrlParam("compare");
          setSelB(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selB]);

  // Fetch the extras for the open run, cached by `slug:runId`. The `cancelled`
  // flag keeps a stale fetch from writing over the state of a newer selection
  // or raising a banner about a run that is no longer open.
  const slugA = selA?.repository ?? null;
  const runIdA = selA?.runId ?? null;
  useEffect(() => {
    if (!slugA || !runIdA) return;
    const key = runKey(slugA, runIdA);
    let cancelled = false;
    void loadRunExtras(
      { slug: slugA, runId: runIdA },
      {
        cached: extrasByRunRef.current[key] ?? {},
        store: (patch) => {
          if (!cancelled)
            setExtrasByRun((m) => ({ ...m, [key]: { ...m[key], ...patch } }));
        },
        onNotice: (message) => {
          if (!cancelled) setNotice(message);
        },
      },
    );
    return () => {
      cancelled = true;
    };
  }, [slugA, runIdA]);

  // Fetch the project config once. Best effort. On failure PR numbers stay
  // plain text, since the link is an enhancement rather than required to render
  // a ref.
  useEffect(() => {
    let cancelled = false;
    api
      .config()
      .then((cfg) => {
        if (!cancelled) setPrUrlTemplate(cfg.pr_url_template);
      })
      .catch(() => {
        /* Left null, so refs still render, only without links. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A pane fetches a case from the repository its run was opened from.
  const openDrawer = useCallback(
    (content: DrawerContent) =>
      void drawerOpener.open(content, (env) => {
        const sel = [selA, selB].find((s) => s?.runId === env.run);
        if (!sel)
          return Promise.reject(new Error(`run ${env.run} is not open`));
        return api.runCase(sel.repository, env.run, env.test, env.case);
      }),
    [drawerOpener, selA, selB],
  );
  // The fetched cases of a run are kept while the run is open.
  useEffect(() => {
    const open = [selA?.runId, selB?.runId].filter(
      (id): id is string => id !== undefined,
    );
    drawerOpener.keepRuns(new Set(open));
  }, [selA, selB, drawerOpener]);

  // Mirror the latest paginated state into refs so `handleLoadMore` reads
  // current offsets regardless of render timing.
  useEffect(() => {
    repositoriesRef.current = repositories;
  }, [repositories]);
  useEffect(() => {
    extrasByRunRef.current = extrasByRun;
  }, [extrasByRun]);

  // Keep the URL in sync with the selection so the view is shareable and a
  // reload lands on the same runs. replaceState rather than push avoids
  // cluttering history on every click. Skips the auto-default so a fresh open
  // recomputes it. Only explicit picks and links are written.
  useEffect(() => {
    if (!selectionIsExplicit.current) return;
    const params = new URLSearchParams(window.location.search);
    if (selA) params.set("run", `${selA.repository}:${selA.runId}`);
    else params.delete("run");
    if (selB) params.set("compare", `${selB.repository}:${selB.runId}`);
    else params.delete("compare");
    const qs = params.toString();
    window.history.replaceState(
      null,
      "",
      qs ? `${window.location.pathname}?${qs}` : window.location.pathname,
    );
  }, [selA, selB]);

  // Slots a fully formed Selection into A or B in chronological order (older =
  // A, newer = B).
  const pickSelection = useCallback(
    (pick: Selection, withModifier: boolean) => {
      selectionIsExplicit.current = true;
      if (withModifier && selA && !selectionEquals(selA, pick)) {
        if (isOlderThan(repositories, pick, selA)) {
          setSelA(pick);
          setSelB(selA);
        } else {
          setSelB(pick);
        }
        return;
      }
      setSelA(pick);
      setSelB(null);
    },
    [selA, repositories],
  );

  const handlePickRun = useCallback(
    (repository: string, runId: string, withModifier: boolean) => {
      pickSelection({ repository, runId }, withModifier);
    },
    [pickSelection],
  );

  const handlePickRef = useCallback(
    (repository: string, refName: string, withModifier: boolean) => {
      // Every rendered ref row carries its tip entry, so the pick resolves
      // from loaded state without a request.
      const ref = loadedRef(repositoriesRef.current, repository, refName);
      if (ref?.error) {
        setNotice(`ref ${refName} cannot be read: ${ref.error}`);
        return;
      }
      const runId = ref?.tip?.run_id ?? null;
      if (runId === null) {
        setNotice(`ref ${refName} points at no run`);
        return;
      }
      pickSelection({ repository, runId, via: refName }, withModifier);
    },
    [pickSelection],
  );

  const handleLoadMore = useCallback(
    async (slug: string, section: SectionKey) => {
      // Mark loading so the button shows its spinner state and disables.
      setRepositories((rs) =>
        rs.map((r) =>
          r.slug === slug
            ? { ...r, [section]: { ...r[section], loading: true } }
            : r,
        ),
      );
      try {
        if (section === "runs") {
          const repo = repositoriesRef.current.find((r) => r.slug === slug);
          if (!repo) return;
          const next = await api.runs(slug, PAGE_SIZE, repo.runs.items.length);
          setRepositories((rs) =>
            rs.map((r) =>
              r.slug === slug
                ? {
                    ...r,
                    runs: {
                      items: [...r.runs.items, ...next],
                      hasMore: next.length === PAGE_SIZE,
                      loading: false,
                    },
                  }
                : r,
            ),
          );
        } else if (section === "baselineLog") {
          const repo = repositoriesRef.current.find((r) => r.slug === slug);
          if (!repo?.baseline) return;
          // The reflog pages newest first, so offsetting by what is loaded
          // fetches the next older entries and history keeps growing downward.
          const next = await api.refLog(
            slug,
            repo.baseline.name,
            PAGE_SIZE,
            repo.baselineLog.items.length,
          );
          setRepositories((rs) =>
            rs.map((r) =>
              r.slug === slug
                ? {
                    ...r,
                    baselineLog: {
                      items: [...r.baselineLog.items, ...next],
                      hasMore: next.length === PAGE_SIZE,
                      loading: false,
                    },
                  }
                : r,
            ),
          );
        }
      } catch (e: unknown) {
        setNotice(String(e));
        // Reset loading flag so the user can retry.
        setRepositories((rs) =>
          rs.map((r) =>
            r.slug === slug
              ? { ...r, [section]: { ...r[section], loading: false } }
              : r,
          ),
        );
      }
    },
    [],
  );

  const deleteRef = useCallback(async (slug: string, name: string) => {
    try {
      const result = await api.deleteRef(slug, name);
      const deleted = new Set(result.deleted_runs);
      // Drop any selection that pointed at a now-deleted run, so the detail
      // pane doesn't try to fetch a 404.
      setSelA((s) =>
        s && s.repository === slug && deleted.has(s.runId) ? null : s,
      );
      setSelB((s) =>
        s && s.repository === slug && deleted.has(s.runId) ? null : s,
      );
      // Update state in place rather than reloading, so the loaded pagination
      // window is preserved. A reload snaps every section back to page 1.
      setRepositories((rs) =>
        rs.map((r) =>
          r.slug === slug ? applyRefDeleted(r, name, deleted) : r,
        ),
      );
    } catch (e: unknown) {
      setNotice(deleteErrorMessage(e));
    }
  }, []);

  const deleteRun = useCallback(async (slug: string, runId: string) => {
    try {
      await api.removeRun(slug, runId);
      setSelA((s) =>
        s && s.repository === slug && s.runId === runId ? null : s,
      );
      setSelB((s) =>
        s && s.repository === slug && s.runId === runId ? null : s,
      );
      // An unreferenced run owns no ref rows, so drop it from the loaded
      // window, preserving pagination (no reload).
      setRepositories((rs) =>
        rs.map((r) =>
          r.slug === slug
            ? {
                ...r,
                runs: {
                  ...r.runs,
                  items: r.runs.items.filter((x) => x.id !== runId),
                },
              }
            : r,
        ),
      );
    } catch (e: unknown) {
      setNotice(deleteErrorMessage(e));
    }
  }, []);

  const handleDeleteRef = useCallback(
    (slug: string, name: string) => {
      setConfirm({
        title: `Delete ref "${name}" and the runs only it reaches?`,
        body: [
          describeRepository(repositoriesRef.current, slug),
          "This cannot be undone. A run that another ref's history also reaches " +
            "is kept.",
        ],
        confirmLabel: "Delete ref",
        run: () => void deleteRef(slug, name),
      });
    },
    [deleteRef],
  );

  const handleDeleteRun = useCallback(
    (slug: string, runId: string) => {
      setConfirm({
        title: `Delete run ${runId.slice(0, 10)}?`,
        body: [
          describeRepository(repositoriesRef.current, slug),
          "This cannot be undone.",
        ],
        confirmLabel: "Delete run",
        run: () => void deleteRun(slug, runId),
      });
    },
    [deleteRun],
  );

  const swap = useCallback(() => {
    selectionIsExplicit.current = true;
    setSelA(selB);
    setSelB(selA);
  }, [selA, selB]);

  // The mainline baseline to compare the open single run against, or null when
  // there's no baseline or the open run *is* the baseline (comparing it to
  // itself is a no-op). Drives the run header's "Compare to mainline" button.
  const baselineForCompare = useMemo((): Selection | null => {
    if (!selA || selB) return null;
    const baseline = resolveBaseline(repositories);
    if (!baseline) return null;
    if (
      selA.repository === baseline.repository &&
      selA.runId === baseline.runId
    ) {
      return null;
    }
    return baseline;
  }, [selA, selB, repositories]);

  // Enter compare mode with BASE = baseline and COMPARE = the run currently open.
  const compareToBaseline = useCallback(() => {
    if (!selA || !baselineForCompare) return;
    selectionIsExplicit.current = true;
    const current = selA;
    setSelA(baselineForCompare);
    setSelB(current);
  }, [selA, baselineForCompare]);

  // Refs in `sel.repository` that currently point at `sel.runId`. The sidebar
  // already fetched every ref's tip entry, so this is a cheap derived view. It
  // feeds RunDetail's "also pointed at by" line.
  const refsForA = useMemo(
    () => refsPointingAt(repositories, selA),
    [repositories, selA],
  );

  const extrasA = selA
    ? extrasByRun[runKey(selA.repository, selA.runId)]
    : undefined;

  const totalRuns = repositories.reduce((n, r) => n + r.runs.items.length, 0);
  const awaiting = awaitingRun(fetchedA, selA) || awaitingRun(fetchedB, selB);

  return (
    <div className="app">
      <Sidebar
        repositories={repositories}
        selA={selA}
        selB={selB}
        prUrlTemplate={prUrlTemplate}
        onPickRun={handlePickRun}
        onPickRef={handlePickRef}
        onLoadMore={(slug, section) => void handleLoadMore(slug, section)}
        onDeleteRef={handleDeleteRef}
        onDeleteRun={handleDeleteRun}
      />
      <main className="main">
        {notice && (
          <div className="notice-banner" role="alert">
            <span className="notice-text">{notice}</span>
            <button
              type="button"
              className="notice-dismiss"
              onClick={dismissNotice}
              aria-label="dismiss"
            >
              ×
            </button>
          </div>
        )}
        {error && (
          <div
            className="empty-state"
            role="alert"
            style={{ color: "var(--fail)", textAlign: "left" }}
          >
            {error}
          </div>
        )}
        {!error && repositories.length === 0 && (
          <div className="empty-state">No repositories mounted.</div>
        )}
        {!error && totalRuns === 0 && repositories.length > 0 && (
          <div className="empty-state">
            <p>No runs yet.</p>
            {/* The first screen for anyone who opened the dashboard before
                recording anything. It has to say how to get a run, not only
                that there isn't one. */}
            <div className="empty-state-help">
              <p>
                Mark an eval with <code>@pytest.mark.evaltrack</code>, then run
                your suite:
              </p>
              <pre>pytest -m evaltrack</pre>
              <p>
                Each pytest session records one run into the repositories listed
                in the sidebar, <code>./.evaltrack</code> by default. Refresh
                this page to see it.
              </p>
            </div>
          </div>
        )}
        {!error && runA && runB && (
          <RunDiff
            a={runA}
            b={runB}
            viaA={selA?.via}
            viaB={selB?.via}
            onSwap={swap}
            onOpenDrawer={openDrawer}
          />
        )}
        {!error && runA && !runB && selA && (
          <RunDetail
            run={runA}
            via={selA.via}
            refs={refsForA}
            slug={selA.repository}
            history={extrasA?.history}
            mainline={extrasA?.mainline}
            prUrlTemplate={prUrlTemplate}
            canCompareToBaseline={baselineForCompare !== null}
            onCompareToBaseline={compareToBaseline}
            onDeleteRun={handleDeleteRun}
            onDeleteRef={handleDeleteRef}
            onOpenDrawer={openDrawer}
            attemptSel={attemptSel}
            onSelectAttempt={selectAttempt}
          />
        )}
        {!error && awaiting && totalRuns > 0 && (
          <div className="empty-state run-loading">
            <span className="spinner" aria-hidden="true" />
            {formatRunProgress(runProgress)}
          </div>
        )}
        {!error && !awaiting && !runA && totalRuns > 0 && (
          <div className="empty-state">
            Pick a run or a ref from the sidebar.
          </div>
        )}
      </main>
      <Drawer
        content={drawer}
        onClose={closeDrawer}
        attemptSel={attemptSel}
        onSelectAttempt={selectAttempt}
      />
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.confirmLabel}
          onConfirm={() => {
            setConfirm(null);
            confirm.run();
          }}
          onCancel={cancelConfirm}
        />
      )}
    </div>
  );
}

/** True when `pick` is older than `existing`. Prefers the cached
 *  RunSummary.created_at. Falls back to ULID lex compare, since ULIDs are
 *  time-sortable by construction. */
function isOlderThan(
  repositories: RepositoryData[],
  pick: Selection,
  existing: Selection,
): boolean {
  const pickTs = lookupCreatedAt(repositories, pick);
  const existingTs = lookupCreatedAt(repositories, existing);
  if (pickTs !== null && existingTs !== null) return pickTs < existingTs;
  return pick.runId < existing.runId;
}

function lookupCreatedAt(
  repositories: RepositoryData[],
  sel: Selection,
): number | null {
  const repo = repositories.find((r) => r.slug === sel.repository);
  const run = repo?.runs.items.find((r: RunSummary) => r.id === sel.runId);
  if (!run) return null;
  const t = Date.parse(run.created_at);
  return Number.isFinite(t) ? t : null;
}

/** A ref as the sidebar has it loaded, or null when it is not loaded. */
function loadedRef(
  repositories: RepositoryData[],
  slug: string,
  name: string,
): Ref | null {
  const repo = repositories.find((r) => r.slug === slug);
  if (!repo) return null;
  if (repo.baseline?.name === name) return repo.baseline;
  return [...repo.prs, ...repo.otherRefs].find((r) => r.name === name) ?? null;
}

function refsPointingAt(
  repositories: RepositoryData[],
  sel: Selection | null,
): Ref[] {
  if (!sel) return [];
  const repo = repositories.find((r) => r.slug === sel.repository);
  if (!repo) return [];
  const refs: Ref[] = [];
  if (repo.baseline?.tip?.run_id === sel.runId) refs.push(repo.baseline);
  for (const r of repo.prs) if (r.tip?.run_id === sel.runId) refs.push(r);
  for (const r of repo.otherRefs) if (r.tip?.run_id === sel.runId) refs.push(r);
  return refs.sort((a, b) => a.name.localeCompare(b.name));
}
