// Mirrors of the JSON the /api/ endpoints return. Kept narrow on purpose. Add a
// field here only when the UI renders it. The stored run keeps every field
// either way, so a missing one here only means the UI does not render it.
// `raw_results` is the one runner-native payload, deliberately left opaque.

// ---- cases ---------------------------------------------------------------

/** Which evaluator produced a result, and how it was configured. `arguments`
 *  is what the evaluator was constructed with, e.g. for
 *  `ToolCalled("addition", params={a:3, b:5})` the tool name plus params, or a
 *  DeepEval metric's own params, so the UI can show what was checked. What the
 *  evaluator answered is on the result, never here. */
interface EvaluatorInfo {
  name: string;
  arguments: Record<string, unknown> | unknown[] | null;
}

/** The shape of a result, for grouping. A number is a score and a boolean an
 *  assertion. Derived from the value with `kindOf`, never stored: what gates a
 *  case is the verdict, so nothing needs the shape written down. */
export type ResultKind = "score" | "assertion";

/** One evaluator's result for one attempt.
 *
 *  `verdict` answers whether it clears the gate, and null means nothing gates
 *  it. Everything else here is what that answer was reached from. */
export interface EvaluatorResult {
  value: number | boolean;
  /** Whether the result passes. An assertion is its own verdict and a number
   *  reaches one through `bar`. Null when nothing gates the result. */
  verdict?: boolean | null;
  /** The number `value` must reach: the marker's bar over the runner's, so this
   *  is the bar the run was gated on. Derived from the two below. */
  bar?: number | null;
  /** The bar the runner set, whether or not the marker's replaced it. */
  runner_bar?: number | null;
  /** The bar the marker declared for this result in `score_bars`. */
  marker_bar?: number | null;
  reason: string | null;
  evaluator: EvaluatorInfo;
  /** Runner-specific data about this result: what the evaluator answered
   *  beyond its value, never how it was configured. Left untyped, since only
   *  the runner that wrote it knows what is in there. */
  details?: Record<string, unknown>;
}

/** Which eval runner produced a test's eval, and at what version. */
interface RunnerInfo {
  name: string;
  version: string | null;
}

/** Outcome of one attempt. `errored` is its own outcome, not a kind of failure.
 *  The attempt reached no verdict, so it is neither a pass nor a fail. */
export type AttemptOutcome = "passed" | "failed" | "errored";

/** Outcome of a case within one run, rolled up from its attempts. */
export type CaseOutcome = "passed" | "failed" | "errored";

/** One attempt at a case. A case has several when test-level reruns or a
 *  native `evaluate(repeat=N)` produced them. Use `findDecidingAttempt` to pick
 *  the representative one rather than indexing. */
export interface AttemptRecord {
  // `passed` means every result that gates the attempt passed. `errored` means
  // the task or an evaluator raised.
  outcome: AttemptOutcome;
  // Per-attempt output, a user-defined value passed through opaquely.
  output: unknown;
  // Each evaluator's result, keyed by result name, with the bar that applied
  // already resolved onto it.
  results: Record<string, EvaluatorResult>;
  // Seconds the task took to produce `output`. Null when the task raised, or
  // when the runner reports no per-case timing.
  task_duration: number | null;
  // Everything that raised while producing or judging this attempt. Empty on a
  // clean attempt, and on runs recorded before errors were captured.
  errors?: AttemptErrorRecord[];
}

/** One thing that raised while producing or judging an attempt. Mirrors the
 *  backend `AttemptErrorRecord`. */
export interface AttemptErrorRecord {
  // The exception's type and message, without a traceback.
  message: string;
  // The evaluator that raised, or null when the task itself did.
  evaluator?: string | null;
}

/** One case. `inputs`, `expected_output` and `metadata` describe it, and
 *  `attempts` hold every try at it. The aggregate fields (`passed_attempts`,
 *  `clean_attempts`, `outcome`, ...) summarise it across those attempts. */
export interface CaseRecord {
  inputs: unknown;
  expected_output: unknown;
  metadata: unknown;
  // Every attempt, earliest first. One per round under flake_reruns, the
  // configured count under repeats. Guard against 0 attempts.
  attempts: AttemptRecord[];
  passed_attempts: number;
  clean_attempts: number;
  // Attempts whose task or an evaluator raised. Never counted in
  // `clean_attempts`.
  errored_attempts: number;
  // Overall per-case outcome. Drives the case pill/styling.
  outcome: CaseOutcome;
}

// ---- reliability ---------------------------------------------------------

/** One run's contribution to a case's cross-run reliability history. Mirrors
 *  the backend `ReliabilityPoint`. */
export interface ReliabilityPoint {
  run_id: string;
  created_at: string;
  commit: string | null;
  passed_attempts: number;
  clean_attempts: number;
  // Per-attempt pass/fail for this run's clean attempts, in the order they ran.
  attempts: boolean[];
  // True for the run being viewed (a PR/local tip) drawn over the mainline
  // history rather than promoted onto it.
  off_mainline: boolean;
}

/** Pooled cross-run reliability for one case over its comparable history.
 *  Mirrors the backend `CaseReliability`. Dashboard-only, never fails a test. */
export interface CaseReliability {
  pooled_attempts: number;
  pooled_passes: number;
  rate: number | null;
  // Lower bound of the 95% Wilson interval on the true pass-rate, or null when
  // no mainline run contributed an attempt.
  lower_bound: number | null;
  target: number | null;
  below_target: boolean;
  pooled_runs: number;
  eval_version: string | null;
  points: ReliabilityPoint[];
  // True when the viewed run's eval_version differs from the mainline tip.
  // There is then no comparable mainline history, and the points are this run
  // alone.
  diverged: boolean;
}

/** Pooled reliability keyed test nodeid -> case id -> reliability. */
export type ReliabilityMap = Record<string, Record<string, CaseReliability>>;

// ---- score history -------------------------------------------------------

/** One run's value for one case's score, plus the run it came from. Mirrors
 *  the backend `ScoreHistoryPoint`. A run can score a case more than once
 *  (reruns, or a native `repeat`), so `value` is their mean and `low`/`high`
 *  carry the spread. */
export interface ScoreHistoryPoint {
  run_id: string;
  created_at: string;
  // The commit on mainline (from the promote entry), or the run's own for a
  // point off the mainline.
  commit: string | null;
  pr: number | null;
  title: string | null;
  value: number;
  low: number;
  high: number;
  attempts: number;
  // True for the run being viewed, drawn over the promoted history.
  off_mainline: boolean;
}

/** One case's history for one score, over that case's own segment. Mirrors the
 *  backend `CaseScoreHistory`. `bar` and `eval_version` are the ones the newest
 *  run of *this case's* segment declared, which is not always the newest run of
 *  the test: a case absent or errored there ends its segment earlier. `points`
 *  run oldest first. */
export interface CaseScoreHistory {
  bar: number | null;
  eval_version: string | null;
  points: ScoreHistoryPoint[];
}

/** One score across the cases of a test's eval. Mirrors the backend
 *  `ScoreHistory`. `cases` is keyed by case id. */
export interface ScoreHistory {
  score: string;
  cases: Record<string, CaseScoreHistory>;
}

/** Score history keyed test name -> one history per score. */
export type ScoreHistoryMap = Record<string, ScoreHistory[]>;

// ---- run history ---------------------------------------------------------

/** The /history response. Both views are measured over the same mainline
 *  history, so one request carries them. Both are empty when the repository has
 *  no mainline history behind it. */
export interface RunHistory {
  reliability: ReliabilityMap;
  score_history: ScoreHistoryMap;
}

/** UI fetch state for a run's cross-run history. The request is lazy and can
 *  take a moment on a remote repository, so the run view distinguishes loading
 *  from ready and error. */
export type HistoryState =
  | { status: "loading" }
  | { status: "ready"; data: RunHistory }
  | { status: "error" };

// ---- runs ----------------------------------------------------------------

/** Fields the listing's summaries and the full run body share. */
interface RunCore {
  id: string;
  created_at: string;
  commit: string | null;
  // true = uncommitted changes when recorded, false = clean tree,
  // null = unknown (no git, env-overridden commit, ...).
  worktree_dirty: boolean | null;
  labels: Record<string, string>;
}

export interface RunSummary extends RunCore {
  // Test counts, so a list of runs can show which are red without loading each
  // body. `tests_failed` counts failed and errored tests. Skipped and xfailed
  // are not failures. Summary only. The full run body carries `tests` instead.
  tests_total: number;
  tests_failed: number;
}

export interface MarkerSettings {
  // Per-case score bars the marker declared, keyed by score name. Recorded for
  // reference: each is already resolved onto the result it applied to, which is
  // where a bar is read from.
  score_bars: Record<string, number>;
  // Attempts required per case, all of which must pass. 1 when the eval ran
  // each case once per round.
  repeats: number;
  // Budget of extra rounds while a case was unproven. 0 when off.
  flake_reruns: number;
  // Target reliability (fraction of attempts that must pass) for the eval, or
  // null when not configured.
  reliability_target: number | null;
  // User-supplied version label for the agent/prompt under test, if any.
  eval_version: string | null;
}

// `xfailed` / `xpassed` mirror pytest's xfail. An eval marked
// `@pytest.mark.xfail` that fails is an acknowledged failure and does not fail
// CI. One that passes is a surprise worth flagging.
export type TestOutcome =
  "passed" | "failed" | "errored" | "skipped" | "xfailed" | "xpassed";

export interface RecordedTest {
  // The test's cases, keyed by case id. A test records at most one eval.
  cases: Record<string, CaseRecord>;
  /** What the marker declared. Null when the test never evaluated, because it
   *  errored or skipped before the eval ran. */
  marker: MarkerSettings | null;
  /** Which eval runner produced this test's eval. Null when the test never
   *  evaluated. */
  runner: RunnerInfo | null;
  // The runner's own result objects, one per round, passed through opaquely
  // for export and download only. The UI renders from `cases`, never these.
  raw_results: unknown[];
  outcome: TestOutcome | null;
  /** The test function's docstring, kept as test-level context (intent, setup
   *  assumptions). Null when the test has no docstring. */
  docstring: string | null;
  /** pytest module path from the nodeid, so relative to pytest's rootdir
   *  rather than to the project root, e.g. "tests/evals/test_x.py". Recorded
   *  even when the test never evaluated. Null when unknown. */
  test_file: string | null;
}

/** Versions of the tools that produced the run. Not on `RunSummary`, because
 *  the sidecar the listing reads does not carry them. */
interface RecordedBy {
  evaltrack: string;
}

export interface RunRecord extends RunCore {
  recorded_by: RecordedBy;
  tests: Record<string, RecordedTest>;
}

// ---- repositories --------------------------------------------------------

/** Presentation role of a mounted repository. `local` is where this project's
 *  own runs go, `remote` the shared repository (baseline, PRs). */
export type RepositoryRole = "local" | "remote";

export interface RepositoryInfo {
  slug: string;
  url: string;
  role: RepositoryRole;
}

/** Project-level dashboard settings, not tied to any one repository. Mirrors the
 *  backend `ProjectConfig`. `pr_url_template` carries a `{pr}` placeholder for
 *  linking a PR ref to its host, or null when unset. */
export interface ProjectConfig {
  pr_url_template: string | null;
}

/** Result of deleting a ref. It names the ref that was removed and the runs it
 *  exclusively owned, which the cascade deleted. Mirrors the backend
 *  `RefDeletion`. */
export interface RefDeletion {
  ref: string;
  deleted_runs: string[];
}

// ---- refs ----------------------------------------------------------------

/** Ref namespace, as classified by the server. The frontend groups by this
 *  instead of parsing ref names. */
type RefKind = "baseline" | "pr" | "other";

/** Where a run landed on the mainline (its most recent `baseline` reflog entry).
 *  Mirrors the backend `MainlineEntry`. Null when the run was never promoted. */
export interface MainlineEntry {
  commit: string | null;
  pr: number | null;
  title: string | null;
  moved_at: string;
}

/** One entry in a ref's reflog. It records the run the ref pointed at, when it
 *  was moved, and the commit recorded on the move. For `baseline` that commit
 *  is the promote or merge commit on main. It also carries the PR number and
 *  title if the move recorded them. Mirrors the backend `ReflogEntry`. It does
 *  not name its ref, because it only ever appears inside one ref's history. */
export interface ReflogEntry {
  run_id: string;
  moved_at: string;
  commit: string | null;
  pr: number | null;
  title: string | null;
  // Summary of the run this entry points at, so a history list shows pass/fail
  // without a fetch per row. Null when that run is no longer readable, and
  // absent on the single-entry `/refs/{name}` response.
  run?: RunSummary | null;
}

/** A ref, with its name, its namespace, and the reflog entry it currently
 *  points at. Mirrors the backend `RefListing`. `tip` is null for a ref whose
 *  reflog is empty, which still lists so it can be deleted. */
export interface Ref {
  name: string;
  tip: ReflogEntry | null;
  kind: RefKind;
  // Summary of the run the ref points at, so a ref list shows pass/fail without
  // a fetch per ref. Null when the ref points at nothing, or at a run that is
  // no longer readable.
  tip_run?: RunSummary | null;
  // Why the ref's history could not be read (a torn reflog, or a key under
  // `refs/` that is not a ref name). `tip` is then null without the ref
  // pointing at nothing. Absent or null for a readable ref.
  error?: string | null;
}
