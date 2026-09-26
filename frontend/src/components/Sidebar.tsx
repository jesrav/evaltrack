import type { ReflogEntry, Ref, RepositoryRole, RunSummary } from "../types";
import { formatBytes, formatPlural } from "../format";
import { buildRunOutcomeChip } from "../runOutcome";
import { shortenUrl } from "../shortenUrl";
import { PrRef } from "./PrRef";
import { ThemeToggle } from "./ThemeToggle";

/** A run identified by both repository and id. Selection model needs both
 *  because the same run id can live in two repositories (after a `push`).
 *  `via` remembers which ref the user clicked to make this selection. Used
 *  by the views to title the run the way the user thinks of it ("baseline")
 *  even when the same run is also pointed at by other refs. */
export interface Selection {
  repository: string;
  runId: string;
  via?: string;
}

/** A section that the sidebar renders and can load more of. */
export interface PaginatedSection<T> {
  items: T[];
  hasMore: boolean;
  loading: boolean;
}

export interface RepositoryData {
  slug: string;
  url: string;
  role: RepositoryRole;
  // True while this repository's initial data is still being fetched. A fast
  // local repo flips to false almost immediately. A slow remote stays true and
  // shows a spinner in its header.
  loading: boolean;
  baseline: Ref | null;
  /** Whole listings, because the ref request returns every ref and nothing pages. */
  prs: Ref[];
  otherRefs: Ref[];
  baselineLog: PaginatedSection<ReflogEntry>;
  runs: PaginatedSection<RunSummary>;
}

interface Props {
  repositories: RepositoryData[];
  selA: Selection | null;
  selB: Selection | null;
  /** Project `{pr}` URL template. When set, PR numbers render as links. */
  prUrlTemplate: string | null;
  onPickRun: (repository: string, runId: string, withModifier: boolean) => void;
  onPickRef: (
    repository: string,
    refName: string,
    withModifier: boolean,
  ) => void;
  onLoadMore: (slug: string, section: SectionKey) => void;
  onDeleteRef: (slug: string, refName: string) => void;
  onDeleteRun: (slug: string, runId: string) => void;
}

export type SectionKey = "baselineLog" | "runs";

export function Sidebar({
  repositories,
  selA,
  selB,
  prUrlTemplate,
  onPickRun,
  onPickRef,
  onLoadMore,
  onDeleteRef,
  onDeleteRun,
}: Props) {
  const totalRuns = repositories.reduce((n, r) => n + r.runs.items.length, 0);
  const totalRefs = repositories.reduce(
    (n, r) => n + (r.baseline ? 1 : 0) + r.prs.length + r.otherRefs.length,
    0,
  );

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <h1 className="brand">
          {/* The evaltrack mark, recreated inline so it tracks the theme
              (accent/pass) and stays crisp. See assets/wordmark.svg. */}
          <svg className="brand-mark" viewBox="0 4 52 44" aria-hidden="true">
            <polyline
              points="6,28 18,40 46,12"
              fill="none"
              stroke="var(--accent)"
              strokeWidth="6.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="6" cy="28" r="3.8" fill="var(--accent)" />
            <circle cx="46" cy="12" r="4.6" fill="var(--pass)" />
          </svg>
          <span className="brand-text">
            <span className="brand-eval">eval</span>
            <span className="brand-track">track</span>
          </span>
        </h1>
        <ThemeToggle />
      </div>
      <p className="brand-sub">
        {repositories.length} repositor
        {repositories.length === 1 ? "y" : "ies"} · showing{" "}
        {formatPlural(totalRuns, "run")} · {formatPlural(totalRefs, "ref")}
      </p>

      {repositories.map((repo) => (
        <RepositoryBlock
          key={repo.slug}
          repo={repo}
          selA={selA}
          selB={selB}
          prUrlTemplate={prUrlTemplate}
          onPickRun={onPickRun}
          onPickRef={onPickRef}
          onLoadMore={onLoadMore}
          onDeleteRef={onDeleteRef}
          onDeleteRun={onDeleteRun}
        />
      ))}

      <p className="hint">
        Click to view. <span className="kbd">⌘/Ctrl</span>+click a second
        run/ref to diff. The two can come from different repositories.
      </p>
    </aside>
  );
}

// ---- per-repository rendering -------------------------------------------

export function repositoryTitle(repo: RepositoryData): string {
  return repo.role === "local" ? "Local" : "Remote";
}

interface RepoBlockProps {
  repo: RepositoryData;
  selA: Selection | null;
  selB: Selection | null;
  prUrlTemplate: string | null;
  onPickRun: (repository: string, runId: string, withModifier: boolean) => void;
  onPickRef: (
    repository: string,
    refName: string,
    withModifier: boolean,
  ) => void;
  onLoadMore: (slug: string, section: SectionKey) => void;
  onDeleteRef: (slug: string, refName: string) => void;
  onDeleteRun: (slug: string, runId: string) => void;
}

function RepositoryBlock(props: RepoBlockProps) {
  const { repo } = props;
  return (
    <details className="repository-block" open>
      <summary className="repository-header">
        <span className="repository-title-row">
          <span className="repository-name">{repositoryTitle(repo)}</span>
          {repo.loading && (
            <span
              className="spinner"
              role="status"
              aria-label="loading repository"
            />
          )}
        </span>
        {/* Shortened, so a repository under a long temp path cannot push the
            run list down the page. The title carries the whole URL. */}
        <span className="repository-url" title={repo.url}>
          {shortenUrl(repo.url)}
        </span>
      </summary>

      {repo.loading ? (
        <p className="repository-loading">Loading…</p>
      ) : (
        <RepositoryBody {...props} />
      )}
    </details>
  );
}

/** The loaded contents of a repository block, the baseline, the ref sections and
 *  the run list. Split out so `RepositoryBlock` can swap in a spinner while the
 *  data is still loading without threading a `loading` branch through every
 *  section. */
function RepositoryBody({
  repo,
  selA,
  selB,
  prUrlTemplate,
  onPickRun,
  onPickRef,
  onLoadMore,
  onDeleteRef,
  onDeleteRun,
}: RepoBlockProps) {
  const hasAnyRefs =
    repo.baseline !== null || repo.prs.length > 0 || repo.otherRefs.length > 0;

  return (
    <>
      {repo.baseline && (
        <MainlineSection
          repo={repo}
          baseline={repo.baseline}
          selA={selA}
          selB={selB}
          prUrlTemplate={prUrlTemplate}
          onPickRun={onPickRun}
          onPickRef={onPickRef}
          onLoadMore={onLoadMore}
        />
      )}

      <RefSection
        title="Pull requests"
        refs={repo.prs}
        repo={repo}
        selA={selA}
        selB={selB}
        prUrlTemplate={prUrlTemplate}
        onPickRef={onPickRef}
        onDeleteRef={onDeleteRef}
        defaultOpen
      />

      <RefSection
        title="Other refs"
        refs={repo.otherRefs}
        repo={repo}
        selA={selA}
        selB={selB}
        prUrlTemplate={prUrlTemplate}
        onPickRef={onPickRef}
        onDeleteRef={onDeleteRef}
        defaultOpen={false}
      />

      {/* Opened by default only when there are no refs. Remote repositories
          with rich refs collapse this. Local dev with no refs keeps the run list
          as the primary navigation. */}
      <details className="runs-section" open={!hasAnyRefs}>
        <summary className="section-title clickable-title">
          All runs ({sectionLabel(repo.runs)})
        </summary>
        <ul className="run-list">
          {repo.runs.items.map((r) => {
            const referencing = referencingRefs(repo, r.id);
            return (
              <li key={r.id} className="run-row-wrap">
                <RunButton
                  run={r}
                  repository={repo.slug}
                  selA={selA}
                  selB={selB}
                  onPickRun={onPickRun}
                />
                {referencing.length > 0 ? (
                  <button
                    type="button"
                    className="ref-delete disabled"
                    aria-disabled="true"
                    title={`Referenced by ${referencing.join(
                      ", ",
                    )}. Delete the ref instead.`}
                    onClick={(e) => e.preventDefault()}
                  >
                    ✕
                  </button>
                ) : (
                  <button
                    type="button"
                    className="ref-delete"
                    title={`Delete run ${r.id}`}
                    aria-label={`Delete run ${r.id}`}
                    onClick={() => onDeleteRun(repo.slug, r.id)}
                  >
                    ✕
                  </button>
                )}
              </li>
            );
          })}
        </ul>
        <LoadMoreButton
          section={repo.runs}
          onClick={() => onLoadMore(repo.slug, "runs")}
        />
      </details>

      {!hasAnyRefs && repo.runs.items.length === 0 && (
        <p className="hint">Empty repository.</p>
      )}
    </>
  );
}

// ---- ref sections -------------------------------------------------------

interface RefSectionProps {
  title: string;
  refs: Ref[];
  repo: RepositoryData;
  selA: Selection | null;
  selB: Selection | null;
  prUrlTemplate: string | null;
  onPickRef: (
    repository: string,
    refName: string,
    withModifier: boolean,
  ) => void;
  onDeleteRef: (slug: string, refName: string) => void;
  defaultOpen: boolean;
}

function RefSection({
  title,
  refs,
  repo,
  selA,
  selB,
  prUrlTemplate,
  onPickRef,
  onDeleteRef,
  defaultOpen,
}: RefSectionProps) {
  // Hidden when empty, so an "Other refs (0)" block takes no space.
  if (refs.length === 0) return null;
  return (
    <details className="ref-section" open={defaultOpen}>
      <summary className="section-title clickable-title">
        {title} ({refs.length})
      </summary>
      <ul className="ref-list">
        {refs.map((r) => (
          <li key={r.name} className="ref-row-wrap">
            <RefButton
              info={r}
              repository={repo.slug}
              selA={selA}
              selB={selB}
              prUrlTemplate={prUrlTemplate}
              onPickRef={onPickRef}
            />
            <button
              type="button"
              className="ref-delete"
              title={`Delete ref ${r.name} and the runs only it reaches`}
              aria-label={`Delete ref ${r.name}`}
              onClick={() => onDeleteRef(repo.slug, r.name)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

interface MainlineProps {
  repo: RepositoryData;
  baseline: Ref;
  selA: Selection | null;
  selB: Selection | null;
  prUrlTemplate: string | null;
  onPickRun: (repository: string, runId: string, withModifier: boolean) => void;
  onPickRef: (
    repository: string,
    refName: string,
    withModifier: boolean,
  ) => void;
  onLoadMore: (slug: string, section: SectionKey) => void;
}

// The heading reads "Mainline" for the promoted line of development, while the
// ref row below it keeps the ref's own name, "baseline".
function MainlineSection({
  repo,
  baseline,
  selA,
  selB,
  prUrlTemplate,
  onPickRun,
  onPickRef,
  onLoadMore,
}: MainlineProps) {
  // Reflog comes back newest first, so the first loaded entry is the current
  // baseline pointer, already rendered as the ref row above. Skip it and show
  // the rest as history. "Load more" appends older entries at the bottom.
  const historicalEntries = repo.baselineLog.items.slice(1);
  const hasHistory =
    historicalEntries.length > 0 || (repo.baselineLog.hasMore ?? false);

  return (
    <details className="ref-section mainline-section" open>
      <summary className="section-title clickable-title">Mainline</summary>
      <ul className="ref-list">
        <li>
          <RefButton
            info={baseline}
            repository={repo.slug}
            selA={selA}
            selB={selB}
            prUrlTemplate={prUrlTemplate}
            onPickRef={onPickRef}
          />
        </li>
      </ul>
      {hasHistory && (
        <details className="reflog-block">
          <summary className="reflog-summary">
            history ({historicalEntries.length}
            {repo.baselineLog.hasMore ? "+" : ""})
          </summary>
          <ul className="reflog-list">
            {historicalEntries.map((entry, i) => (
              <li key={`${entry.run_id}-${i}`}>
                <ReflogButton
                  entry={entry}
                  repository={repo.slug}
                  selA={selA}
                  selB={selB}
                  prUrlTemplate={prUrlTemplate}
                  onPickRun={onPickRun}
                />
              </li>
            ))}
          </ul>
          <LoadMoreButton
            section={repo.baselineLog}
            onClick={() => onLoadMore(repo.slug, "baselineLog")}
          />
        </details>
      )}
    </details>
  );
}

// ---- shared row + button components -------------------------------------

function LoadMoreButton({
  section,
  onClick,
}: {
  section: { hasMore: boolean; loading: boolean };
  onClick: () => void;
}) {
  if (!section.hasMore && !section.loading) return null;
  return (
    <button
      type="button"
      className="load-more"
      onClick={onClick}
      disabled={section.loading || !section.hasMore}
    >
      {section.loading ? "loading…" : "load more"}
    </button>
  );
}

function sectionLabel<T>(section: PaginatedSection<T>): string {
  const shown = section.items.length;
  return section.hasMore ? `${shown}+` : `${shown}`;
}

function RunButton({
  run,
  repository,
  selA,
  selB,
  onPickRun,
}: {
  run: RunSummary;
  repository: string;
  selA: Selection | null;
  selB: Selection | null;
  onPickRun: (repository: string, runId: string, withModifier: boolean) => void;
}) {
  const cls = runRowClass(repository, run.id, selA, selB);
  return (
    <button
      className={cls}
      type="button"
      onClick={(e) => onPickRun(repository, run.id, e.metaKey || e.ctrlKey)}
      title={run.id}
    >
      <div className="run-row-head">
        <span>{run.id.slice(0, 10)}</span>
        <RunOutcomeChip run={run} />
      </div>
      <div className="meta">
        {run.commit ? run.commit.slice(0, 8) : "no-commit"}
        {run.worktree_dirty === true && (
          <span
            className="dirty-tag"
            title="working tree had uncommitted changes"
          >
            dirty
          </span>
        )}
        {" · "}
        {formatRelative(run.created_at)}
        {run.size_bytes != null && (
          <>
            {" · "}
            <span title="stored size of the run">
              {formatBytes(run.size_bytes)}
            </span>
          </>
        )}
      </div>
    </button>
  );
}

/** Pass/fail at a glance, so picking a run out of a list does not mean opening
 *  each one. A run that recorded no tests gets no chip, because it has no
 *  outcome and a green tick is wrong. */
function RunOutcomeChip({ run }: { run: RunSummary }) {
  const chip = buildRunOutcomeChip(run.tests_total, run.tests_failed);
  if (chip === null) return null;
  return (
    <span
      className={`run-outcome ${chip.failing ? "fail" : "pass"}`}
      title={chip.title}
    >
      {chip.label}
    </span>
  );
}

function RefButton({
  info,
  repository,
  selA,
  selB,
  prUrlTemplate,
  onPickRef,
}: {
  info: Ref;
  repository: string;
  selA: Selection | null;
  selB: Selection | null;
  prUrlTemplate: string | null;
  onPickRef: (
    repository: string,
    refName: string,
    withModifier: boolean,
  ) => void;
}) {
  const tip = info.tip;
  const cls = refRowClass(repository, tip?.run_id ?? null, selA, selB);
  // The ref's own tip commit (for baseline, the commit on main), not the
  // pointed-at run's eval-time commit. Independent of which runs are paginated in,
  // and consistent with the reflog history rows below.
  const commitShort = tip?.commit?.slice(0, 8) ?? "no-commit";
  // A ref whose history cannot be read has no tip to show, and the row must not
  // read as one that points at nothing. The full error goes in the title, the
  // same one the CLI listing prints, so the row itself stays one line.
  if (info.error) {
    return (
      <button
        className={cls}
        type="button"
        onClick={(e) =>
          onPickRef(repository, info.name, e.metaKey || e.ctrlKey)
        }
        title={`${repository} · ${info.name} · ${info.error}`}
      >
        <div className="run-row-head">
          <span className="ref-row-name">{info.name}</span>
        </div>
        <div className="meta">
          <span className="ref-unreadable">unreadable history</span>
        </div>
      </button>
    );
  }
  return (
    <button
      className={cls}
      type="button"
      onClick={(e) => onPickRef(repository, info.name, e.metaKey || e.ctrlKey)}
      title={`${repository} · ${info.name} → ${tip?.run_id ?? "nothing"}`}
    >
      <div className="run-row-head">
        <span className="ref-row-name">{info.name}</span>
        {info.tip_run && <RunOutcomeChip run={info.tip_run} />}
      </div>
      <div className="meta">
        {commitShort}
        {tip?.pr != null && (
          <>
            {" · "}
            <PrRef pr={tip.pr} title={tip.title} template={prUrlTemplate} />
          </>
        )}
        {" · "}
        {/* A ref whose reflog is empty points nowhere and was never moved. */}
        {tip !== null
          ? `moved ${formatRelative(tip.moved_at)}`
          : "points at nothing"}
      </div>
    </button>
  );
}

function ReflogButton({
  entry,
  repository,
  selA,
  selB,
  prUrlTemplate,
  onPickRun,
}: {
  entry: ReflogEntry;
  repository: string;
  selA: Selection | null;
  selB: Selection | null;
  prUrlTemplate: string | null;
  onPickRun: (repository: string, runId: string, withModifier: boolean) => void;
}) {
  const cls = runRowClass(repository, entry.run_id, selA, selB);
  const hasPr = entry.pr !== null && entry.pr !== undefined;
  return (
    <button
      className={cls}
      type="button"
      onClick={(e) =>
        onPickRun(repository, entry.run_id, e.metaKey || e.ctrlKey)
      }
      title={entry.run_id}
    >
      <div className="run-row-head">
        <span className="meta">
          {entry.commit ? entry.commit.slice(0, 8) : "no-commit"}
          {hasPr && (
            <>
              {" · "}
              <PrRef
                pr={entry.pr as number}
                title={entry.title}
                template={prUrlTemplate}
              />
            </>
          )}
        </span>
        {entry.run && <RunOutcomeChip run={entry.run} />}
      </div>
      <div className="meta">{formatRelative(entry.moved_at)}</div>
    </button>
  );
}

// ---- helpers ------------------------------------------------------------

/** Refs that keep `runId` reachable, from the data already loaded in the
 *  sidebar. That is any current ref pointer at it, plus `baseline` when it is in
 *  the loaded baseline history. Best effort over loaded items only, so a ref
 *  beyond the paginated window does not show here. The server's 409 is the real
 *  guard, and this only disables the obvious cases. */
function referencingRefs(repo: RepositoryData, runId: string): string[] {
  const names: string[] = [];
  if (repo.baseline?.tip?.run_id === runId) names.push(repo.baseline.name);
  for (const r of repo.prs) if (r.tip?.run_id === runId) names.push(r.name);
  for (const r of repo.otherRefs)
    if (r.tip?.run_id === runId) names.push(r.name);
  if (
    !names.includes("baseline") &&
    repo.baselineLog.items.some((e) => e.run_id === runId)
  ) {
    names.push("baseline");
  }
  return names;
}

function runRowClass(
  repository: string,
  runId: string,
  selA: Selection | null,
  selB: Selection | null,
): string {
  if (selA && selA.repository === repository && selA.runId === runId) {
    return "run-row selected-a";
  }
  if (selB && selB.repository === repository && selB.runId === runId) {
    return "run-row selected-b";
  }
  return "run-row";
}

function refRowClass(
  repository: string,
  pointsAt: string | null,
  selA: Selection | null,
  selB: Selection | null,
): string {
  if (selA && selA.repository === repository && selA.runId === pointsAt) {
    return "ref-row selected-a";
  }
  if (selB && selB.repository === repository && selB.runId === pointsAt) {
    return "ref-row selected-b";
  }
  return "ref-row";
}

// Quick, dependency-free relative-time formatter. Good enough for sidebar use.
// Switch to Intl.RelativeTimeFormat or date-fns if precision matters later.
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const m = Math.round(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}
