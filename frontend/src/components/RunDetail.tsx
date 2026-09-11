import type { ReactNode } from "react";
import { resultsOfKind } from "../results";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api";
import { formatAttemptCounts } from "../attemptCounts";
import { summariseErrors } from "../attemptErrors";
import type { CaseFilter } from "../caseFilter";
import { NO_CASE_FILTER, filterCases, isCaseFilterActive } from "../caseFilter";
import type { CaseFocus } from "../caseSections";
import {
  OUTCOME_LABEL,
  OUTCOME_MARK,
  findDecidingAttemptIndex,
  recoveredOnARerun,
} from "../checks";
import { isClamped } from "../clamp";
import { formatCanonicalText, formatDisplayText, truncate } from "../extract";
import { formatDuration, formatRatePct, formatPlural } from "../format";
import type { ModuleGroup } from "../grouping";
import { groupTestsByModule } from "../grouping";
import { buildReliabilityNoteLines } from "../reliabilityNote";
import { buildCaseTrends } from "../scoreTrend";
import type { CaseTrend } from "../scoreTrend";

import type { DrawerContent } from "./Drawer";
import { EvaluatorList } from "./EvaluatorList";
import { PrRef } from "./PrRef";
import { TableScroll } from "./TableScroll";
import type {
  AttemptRecord,
  CaseReliability,
  CaseRecord,
  RunRecord,
  MainlineEntry,
  Ref,
  HistoryState,
  ReliabilityPoint,
  MarkerSettings,
  ScoreHistory,
  TestOutcome,
  RecordedTest,
} from "../types";

interface Props {
  run: RunRecord;
  /** Ref the user clicked to reach this run (undefined if they clicked the
   *  run id directly). Becomes the title when present. */
  via?: string;
  /** All refs in the same repository that currently point at this run. Rendered
   *  as chips. A ref whose tip records a PR shows its number and title too. */
  refs: Ref[];
  /** Repository slug, needed to address delete actions. */
  slug: string;
  /** Cross-run reliability and score history for this run's repository,
   *  fetched separately and lazily. `loading` shows a spinner in the
   *  Reliability column. `error` (or absent) degrades to no column and no
   *  trend panels. */
  history?: HistoryState;
  /** Where this run landed on main (promote commit + PR), or null if it was never
   *  promoted / undefined while loading. Shown next to the eval-time commit. */
  mainline?: MainlineEntry | null;
  /** Project `{pr}` URL template. When set, the mainline PR number is a link. */
  prUrlTemplate: string | null;
  /** Whether a "Compare to mainline" action is available for this run (a
   *  baseline ref exists and this run isn't itself the baseline). */
  canCompareToBaseline: boolean;
  onCompareToBaseline: () => void;
  onDeleteRun: (slug: string, runId: string) => void;
  onDeleteRef: (slug: string, refName: string) => void;
  onOpenDrawer: (content: DrawerContent) => void;
  /** Selected attempt index per case, keyed by case title. Unset means the
   *  deciding attempt. Drives which attempt the row and its field panes show. */
  attemptSel: Record<string, number>;
  onSelectAttempt: (key: string, index: number) => void;
}

export function RunDetail({
  run,
  via,
  refs,
  slug,
  history,
  mainline,
  prUrlTemplate,
  canCompareToBaseline,
  onCompareToBaseline,
  onDeleteRun,
  onDeleteRef,
  onOpenDrawer,
  attemptSel,
  onSelectAttempt,
}: Props) {
  const [filter, setFilter] = useState<CaseFilter>(NO_CASE_FILTER);
  const filtered = isCaseFilterActive(filter);
  const modules = shownModules(run, filter);
  const counts = countCases(run, filter);
  const ready = history?.status === "ready" ? history.data : undefined;
  const reliabilityMap = ready?.reliability;
  const reliabilityLoading = !history || history.status === "loading";
  const trendMap = ready?.score_history;
  return (
    <div>
      <RunHeader
        run={run}
        via={via}
        refs={refs}
        slug={slug}
        mainline={mainline}
        prUrlTemplate={prUrlTemplate}
        canCompareToBaseline={canCompareToBaseline}
        onCompareToBaseline={onCompareToBaseline}
        onDeleteRun={onDeleteRun}
        onDeleteRef={onDeleteRef}
      />
      {counts.total > 0 && (
        <CaseFilters
          filter={filter}
          onChange={setFilter}
          shown={counts.shown}
          total={counts.total}
        />
      )}
      {counts.total === 0 && modules.length === 0 && (
        <div className="empty-state">
          This run recorded no evals. A test records one when it is marked{" "}
          <code>@pytest.mark.evaltrack</code> and calls{" "}
          <code>Dataset.evaluate()</code>.
        </div>
      )}
      {filtered && modules.length === 0 && counts.total > 0 && (
        <div className="empty-state">No case matches the filter.</div>
      )}
      {modules.map((mod) => (
        <details
          className="module-block"
          key={mod.module}
          // A filter asks to see what it kept, so a module that kept anything
          // opens, red or not.
          open={filtered || mod.summary.failing > 0 || modules.length === 1}
        >
          <summary className="module-header">
            <span className="module-caret" aria-hidden="true">
              ▸
            </span>
            <span className="module-path">{mod.module}</span>
            <ModuleStatusBadge summary={mod.summary} />
          </summary>
          {mod.tests.map(({ nodeid, test, settings, cases, totalCases }) => {
            const testOutcome = test.outcome ?? undefined;
            const unenforced =
              testOutcome === "passed" &&
              cases.some(([, c]) => c.outcome !== "passed");
            const xfail =
              testOutcome === "xfailed" || testOutcome === "xpassed"
                ? testOutcome
                : null;
            // Open when there is something to look at, because the test failed
            // or errored or any case verdict failed (including not enforced).
            // Passing tests collapse so failures stand out.
            const failing =
              testOutcome === "failed" ||
              testOutcome === "errored" ||
              cases.some(([, c]) => c.outcome !== "passed");
            const trends = trendMap?.[nodeid];
            return (
              <details
                className="report-block"
                key={nodeid}
                open={filtered || failing}
              >
                <summary className="report-header">
                  <span className="report-caret">▸</span>
                  <span className="report-name">{nodeid}</span>
                  <TestOutcomeBadge testOutcome={testOutcome} />
                  {xfail && <XfailChip outcome={xfail} />}
                  <ReportConfigChips settings={settings} />
                </summary>
                <TestContext docstring={test.docstring ?? null} />
                {unenforced && <NotEnforcedNote />}
                {xfail && <XfailNote outcome={xfail} />}
                <CaseTable
                  cases={cases}
                  totalCases={totalCases}
                  testLabel={nodeid}
                  testOutcome={testOutcome}
                  reliabilityByCase={reliabilityMap?.[nodeid]}
                  reliabilityLoading={reliabilityLoading}
                  trends={trends}
                  onOpenDrawer={onOpenDrawer}
                  attemptSel={attemptSel}
                  onSelectAttempt={onSelectAttempt}
                />
              </details>
            );
          })}
          {mod.unevaluatedTests.map((nodeid) => (
            <UnevaluatedTest
              key={nodeid}
              nodeid={nodeid}
              outcome={run.tests[nodeid]?.outcome ?? undefined}
            />
          ))}
        </details>
      ))}
    </div>
  );
}

/** One test the run view draws, with its case rows already narrowed by the
 *  filter. `totalCases` is what the test recorded, so the table can say how
 *  many rows the filter took away. */
interface ShownTest {
  nodeid: string;
  test: RecordedTest;
  settings: MarkerSettings;
  cases: [string, CaseRecord][];
  totalCases: number;
}

/** One module the run view draws. `summary` counts the whole module whatever
 *  the filter hides. The badge says how the module is doing, which the reader's
 *  filter must not change. */
interface ShownModule {
  module: string;
  summary: ModuleSummary;
  tests: ShownTest[];
  unevaluatedTests: string[];
}

/** The modules, tests and case rows left after the filter.
 *
 *  A test with no matching case drops out, and so does a module left with
 *  nothing at all. Only an active filter removes anything. With the controls at
 *  their defaults this is the whole run, tests with no cases included. */
export function shownModules(
  run: RunRecord,
  filter: CaseFilter,
): ShownModule[] {
  const filtered = isCaseFilterActive(filter);
  const out: ShownModule[] = [];
  for (const group of groupTestsByModule(run)) {
    const tests: ShownTest[] = [];
    for (const nodeid of group.tests) {
      const test = run.tests[nodeid];
      if (!test?.marker) continue;
      const all = Object.entries(test.cases);
      const cases = filterCases(all, filter);
      if (filtered && cases.length === 0) continue;
      tests.push({
        nodeid,
        test,
        settings: test.marker,
        cases,
        totalCases: all.length,
      });
    }
    // A test that recorded no eval has no case id to match, so a name filter
    // always drops it. Under "failing only" its own failure still belongs on
    // screen, because it is one of the failures the reader asked for.
    const unevaluatedTests = group.unevaluatedTests.filter((name) => {
      if (filter.name.trim() !== "") return false;
      if (!filter.failingOnly) return true;
      const outcome = run.tests[name]?.outcome;
      return outcome === "failed" || outcome === "errored";
    });
    if (filtered && tests.length === 0 && unevaluatedTests.length === 0)
      continue;
    out.push({
      module: group.module,
      summary: summariseModule(group, run),
      tests,
      unevaluatedTests,
    });
  }
  return out;
}

/** Case rows the filter keeps, against the run's recorded total. Feeds the one
 *  line that tells the reader something is hidden. */
export function countCases(
  run: RunRecord,
  filter: CaseFilter,
): { shown: number; total: number } {
  let shown = 0;
  let total = 0;
  for (const test of Object.values(run.tests)) {
    const cases = Object.entries(test.cases);
    total += cases.length;
    shown += filterCases(cases, filter).length;
  }
  return { shown, total };
}

/** Whether a case row shows the attempt's assertion and score pills. An
 *  errored attempt reached no verdict and the drawer shows no scores for it,
 *  so a pill would open the drawer with nothing to scroll to. */
export function attemptShowsResults(
  attempt: AttemptRecord | null | undefined,
): attempt is AttemptRecord {
  return attempt != null && attempt.outcome !== "errored";
}

/** Whether a test's case table carries an Expected column.
 *
 *  Only for cases written against a target, and only when the target and what
 *  the run produced actually differ somewhere. A strict comparison eval that
 *  passes every case otherwise prints the same value twice, in the widest
 *  pair of columns in the table. A case with no attempt to compare against
 *  keeps the column, since the target is then the only thing to read. */
export function showsExpectedColumn(cases: CaseRecord[]): boolean {
  return cases.some((c) => {
    if (c.expected_output == null) return false;
    const attempts = c.attempts;
    const attempt = attempts[findDecidingAttemptIndex(c)] ?? attempts[0];
    if (attempt === undefined) return true;
    return (
      formatCanonicalText(c.expected_output) !==
      formatCanonicalText(attempt.output)
    );
  });
}

/** The two case filters, above the modules they narrow. The run header and the
 *  module badges keep counting the whole run, so the note here is the only
 *  place that says how much is on screen. */
function CaseFilters({
  filter,
  onChange,
  shown,
  total,
}: {
  filter: CaseFilter;
  onChange: (filter: CaseFilter) => void;
  shown: number;
  total: number;
}) {
  return (
    <div className="case-filters">
      <input
        type="search"
        className="case-filter-name"
        placeholder="Filter cases by name"
        aria-label="Filter cases by name"
        value={filter.name}
        onChange={(e) => onChange({ ...filter, name: e.target.value })}
      />
      <button
        type="button"
        className={`filter-toggle${filter.failingOnly ? " active" : ""}`}
        aria-pressed={filter.failingOnly}
        title="Show only the cases that failed or errored"
        onClick={() =>
          onChange({ ...filter, failingOnly: !filter.failingOnly })
        }
      >
        ✗ Failing only
      </button>
      {isCaseFilterActive(filter) && (
        <span className="case-filter-note">
          {shown} of {formatPlural(total, "case")} shown
        </span>
      )}
    </div>
  );
}

/** Every eval runner that produced something in this run, as "name version".
 *  The runner is per test, because one session can record several. */
export function describeRunners(run: RunRecord): string[] {
  const seen = new Set<string>();
  for (const test of Object.values(run.tests)) {
    if (!test.runner) continue;
    seen.add(
      test.runner.version
        ? `${test.runner.name} ${test.runner.version}`
        : test.runner.name,
    );
  }
  return [...seen].sort();
}

/** Small chips on the test header describing the config that shapes the
 *  numbers, the cross-run reliability target and the SUT version. Renders
 *  nothing when both are at their default. The test's docstring lives in
 *  `TestContext`, shown when the test is expanded. */
function ReportConfigChips({ settings }: { settings: MarkerSettings }) {
  const hasTarget = settings.reliability_target != null;
  const hasVersion = !!settings.eval_version;
  if (!hasTarget && !hasVersion) return null;
  return (
    <span className="gate-chips">
      {hasTarget && (
        <span
          className="gate-chip"
          title="Cross-run reliability target. Surfaced in the dashboard and never fails a build."
        >
          reliability ≥ {settings.reliability_target}
        </span>
      )}
      {hasVersion && (
        <span className="gate-chip" title="System-under-test version label.">
          version {settings.eval_version}
        </span>
      )}
    </span>
  );
}

/** Labeled test-level context shown when the test is expanded: the test
 *  docstring. */
function TestContext({ docstring }: { docstring: string | null }) {
  if (!docstring) return null;
  return (
    <div className="test-context">
      <div className="test-context-row">
        <span className="test-context-label">Docstring</span>
        <Docstring text={docstring} />
      </div>
    </div>
  );
}

/** Report-header pill driven purely by the captured pytest outcome. The test
 *  outcome is the only report-level verdict signal. Per-case pass/fail lives in
 *  the table below. */
function TestOutcomeBadge({
  testOutcome,
}: {
  testOutcome: TestOutcome | undefined;
}) {
  if (testOutcome === undefined) {
    return (
      <span
        className="report-verdict no-checks"
        title="pytest recorded no outcome for this test. The per-case verdicts below are all there is."
      >
        outcome not recorded
      </span>
    );
  }
  if (testOutcome === "skipped") {
    return <span className="report-verdict no-checks">skipped</span>;
  }
  if (testOutcome === "errored") {
    return (
      <span
        className="report-verdict fail"
        title="pytest reported an error during this test (not a regular assertion failure)."
      >
        ⚠ errored
      </span>
    );
  }
  // The badge shows the actual eval result. The xfail marker is annotated
  // separately by the `xfail` chip and note, so an xpass shows as the pass it
  // is, and an xfailed as the failure it is.
  if (testOutcome === "passed" || testOutcome === "xpassed") {
    return <span className="report-verdict pass">Test ✓</span>;
  }
  return <span className="report-verdict fail">Test ✗</span>;
}

/** A test that recorded no eval. It was skipped, or it failed or errored before
 *  `evaluate()`. Rendered as a bare row under its module, so the rows on screen
 *  match the module badge and the run header. */
function UnevaluatedTest({
  nodeid,
  outcome,
}: {
  nodeid: string;
  outcome: TestOutcome | undefined;
}) {
  return (
    <div className="report-block unevaluated">
      <div className="report-header">
        {/* Invisible caret keeps the name aligned with the expandable report
            rows above, which carry a real ▸ caret. */}
        <span
          className="report-caret report-caret-placeholder"
          aria-hidden="true"
        >
          ▸
        </span>
        <span className="report-name">{nodeid}</span>
        <TestOutcomeBadge testOutcome={outcome} />
        <span className="hint">{unevaluatedHint(outcome)}</span>
      </div>
    </div>
  );
}

function unevaluatedHint(outcome: TestOutcome | undefined): string {
  if (outcome === "skipped") {
    return "pytest skipped this test, so the eval never ran";
  }
  if (outcome === "errored" || outcome === "failed") {
    const verb = outcome === "errored" ? "errored" : "failed";
    return `no eval recorded, the test ${verb} before evaluating`;
  }
  return "no eval recorded, this test never reached evaluate()";
}

/** Yellow header chip flagging that the test carries pytest's `xfail` marker.
 *  Shown for both an expected failure (`xfailed`) and an unexpected pass
 *  (`xpassed`). The full explanation lives in `XfailNote`. */
function XfailChip({ outcome }: { outcome: "xfailed" | "xpassed" }) {
  return (
    <span
      className="gate-chip xfail-chip"
      title={
        outcome === "xpassed"
          ? "Marked xfail, but the eval passed. The xfail marker can probably be removed."
          : "Marked xfail: this failure is expected and does not fail CI."
      }
    >
      xfail
    </span>
  );
}

/** Click-to-expand explanation of the test's `xfail` state, shown under the
 *  report header for an `xfailed` / `xpassed` test. */
function XfailNote({ outcome }: { outcome: "xfailed" | "xpassed" }) {
  const passed = outcome === "xpassed";
  return (
    <details className="info-note">
      <summary>
        <span className="info-icon" aria-hidden="true">
          ⓘ
        </span>{" "}
        {passed
          ? "Marked xfail, but it passed. What's this?"
          : "Marked xfail (expected failure). What's this?"}
      </summary>
      <p>
        This test carries pytest's <code>@pytest.mark.xfail</code> marker.{" "}
        {passed
          ? "The eval passed even though it's marked as a known failure, so pytest reports an unexpected pass (xpass). It works now, so the xfail marker can probably go."
          : "Its failure is expected: the eval is recorded as failed, but the build stays green. Remove the marker once it's fixed."}
      </p>
    </details>
  );
}

/** The test function's docstring as test-level context. Collapsed to a couple
 *  of lines by default (eval docstrings can be long). A "more/less" control
 *  appears only when the text is actually clamped, so a docstring that fits is
 *  plain text with no false pressability. The control is a real button,
 *  keyboard-accessible and announced as a toggle. The text itself is never
 *  pressable. */
function Docstring({ text }: { text: string }) {
  const textRef = useRef<HTMLSpanElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [truncated, setTruncated] = useState(false);

  // Truncation is a layout property (it depends on width and wrapping), so
  // measure it rather than guess from the string. Re-measure on resize, but
  // only while collapsed. Once expanded there is no clamp to overflow and the
  // measure falsely reports "fits".
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    const measure = () => {
      if (!expanded) setTruncated(isClamped(el.scrollHeight, el.clientHeight));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // `text` is a dependency so a changed docstring re-measures directly, not
    // only via the ResizeObserver noticing the span's size change.
  }, [expanded, text]);

  return (
    <div className={`test-docstring${expanded ? " expanded" : ""}`}>
      <span className="test-docstring-text" ref={textRef}>
        {text}
      </span>
      {truncated && (
        <button
          type="button"
          className="test-docstring-cue"
          aria-expanded={expanded}
          title={
            expanded
              ? "Test docstring. Click to collapse."
              : "Test docstring (test-level context). Click to expand."
          }
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "▾ less" : "▸ more"}
        </button>
      )}
    </div>
  );
}

/** Click-to-expand explanation shown once per report when the test passed but
 *  some case verdicts failed. More discoverable than a per-pill hover, and it
 *  works on touch. */
function NotEnforcedNote() {
  return (
    <details className="info-note">
      <summary>
        <span className="info-icon" aria-hidden="true">
          ⓘ
        </span>{" "}
        Some cases failed but the test passed. Why?
      </summary>
      <p>
        Under the marker a failing case fails the test, so the test must have
        handled the failure itself instead of letting it propagate. The affected
        cases are marked <span className="pill not-enforced">not enforced</span>{" "}
        below. To record a failing eval on purpose without failing CI, mark the
        test <code>xfail</code>. That shows as an expected failure, not "not
        enforced".
      </p>
    </details>
  );
}

interface ModuleSummary {
  total: number;
  failing: number;
  xfailed: number;
  passing: number;
  skipped: number;
}

/** Rollup for a module's badge. Counts every test the module renders, including
 *  the unevaluated ones. The badge, the run header and the rows on screen then
 *  show the same totals. */
export function summariseModule(
  group: ModuleGroup,
  run: RunRecord,
): ModuleSummary {
  let total = 0;
  let failing = 0;
  let xfailed = 0;
  let passing = 0;
  let skipped = 0;
  for (const nodeid of [...group.tests, ...group.unevaluatedTests]) {
    const test = run.tests[nodeid];
    if (!test) continue;
    total += 1;
    const outcome = test.outcome ?? undefined;
    if (outcome === "failed" || outcome === "errored") {
      failing += 1;
    } else if (outcome === undefined) {
      // No pytest outcome, so fall back to the per-case verdicts and a test
      // still reports as failing when any case failed.
      if (Object.values(test.cases).some((c) => c.outcome !== "passed"))
        failing += 1;
    } else if (outcome === "xfailed") {
      // An acknowledged (expected) failure. It keeps CI green but is not a pass.
      xfailed += 1;
    } else if (outcome === "skipped") {
      skipped += 1;
    } else {
      passing += 1; // passed / xpassed
    }
  }
  return { total, failing, xfailed, passing, skipped };
}

/** The module badge, a verdict plus a count of the skipped evals when there are
 *  any. A skip is neither a pass nor a failure, so it gets its own chip. */
function ModuleStatusBadge({ summary }: { summary: ModuleSummary }) {
  return (
    <span className="module-status-group">
      <ModuleVerdictBadge summary={summary} />
      {summary.skipped > 0 && (
        <span
          className="module-status muted"
          title="Evals in this module that pytest skipped, so they recorded no result."
        >
          {summary.skipped} skipped
        </span>
      )}
    </span>
  );
}

function ModuleVerdictBadge({ summary }: { summary: ModuleSummary }) {
  if (summary.total === 0) {
    return <span className="module-status muted">no evals recorded</span>;
  }
  // If every test was skipped, the skipped chip already says so. A verdict as
  // well counts them twice.
  if (summary.skipped === summary.total) return null;
  if (summary.failing > 0) {
    return (
      <span className="module-status fail">
        ✗ {summary.failing} of {formatPlural(summary.total, "test")} failing
      </span>
    );
  }
  if (summary.xfailed > 0) {
    // Acknowledged (expected) failures. CI is green, but flag them amber. Keep
    // the passing count when the module is mixed so good news isn't hidden.
    const xfailText = formatPlural(summary.xfailed, "expected failure");
    return (
      <span className="module-status warn">
        {summary.passing > 0
          ? `✓ ${summary.passing} of ${formatPlural(summary.total, "test")} passing · ${xfailText}`
          : `⊗ ${xfailText}`}
      </span>
    );
  }
  if (summary.passing === 0) {
    return <span className="module-status muted">no verdict recorded</span>;
  }
  if (summary.passing === summary.total) {
    return (
      <span className="module-status pass">
        ✓ {formatPlural(summary.passing, "test")} passing
      </span>
    );
  }
  return (
    <span className="module-status pass">
      ✓ {summary.passing} of {formatPlural(summary.total, "test")} passing
    </span>
  );
}

/** Run-wide rollup for the header verdict. Tests are failing when pytest
 *  reported failed/errored, or, when no pytest outcome was captured, when any of
 *  their cases failed. A case that failed while its test still passed is "not
 *  enforced", meaning the test handled the failure instead of letting it
 *  propagate. An
 *  `xfailed` test is an acknowledged failure that keeps CI green. */
interface RunStats {
  tests: number;
  failingTests: number;
  xfailedTests: number;
  /** Tests pytest reported as passed or xpassed. A skipped test counts in
   *  `tests` but not here or in `failingTests`, so "nothing failed" is not the
   *  same as "passed". */
  passingTests: number;
  cases: number;
  failingCases: number;
  unenforced: number;
}

export function summariseRun(run: RunRecord): RunStats {
  let tests = 0;
  let failingTests = 0;
  let xfailedTests = 0;
  let passingTests = 0;
  let cases = 0;
  let failingCases = 0;
  let unenforced = 0;
  for (const test of Object.values(run.tests)) {
    tests += 1;
    const outcome = test.outcome ?? undefined;
    if (outcome === "xfailed") xfailedTests += 1;
    if (outcome === "passed" || outcome === "xpassed") passingTests += 1;
    const allCases = Object.values(test.cases);
    const anyCaseFailed = allCases.some((c) => c.outcome !== "passed");
    const testFailing =
      outcome === "failed" ||
      outcome === "errored" ||
      (outcome === undefined && anyCaseFailed);
    if (testFailing) failingTests += 1;
    for (const c of allCases) {
      cases += 1;
      if (c.outcome !== "passed") {
        failingCases += 1;
        if (outcome === "passed") unenforced += 1;
      }
    }
  }
  return {
    tests,
    failingTests,
    xfailedTests,
    passingTests,
    cases,
    failingCases,
    unenforced,
  };
}

type RunVerdictKind = "failed" | "xfailed" | "unenforced" | "passed" | "none";

/** Which headline the run gets. `none` means no test passed or failed,
 *  because every test skipped. Nothing failed, but a pass claims an outcome no
 *  test recorded. */
export function runVerdictKind(stats: RunStats): RunVerdictKind {
  if (stats.failingTests > 0) return "failed";
  if (stats.xfailedTests > 0) return "xfailed";
  if (stats.unenforced > 0) return "unenforced";
  if (stats.passingTests === 0) return "none";
  return "passed";
}

/** Headline pass/fail pill for the whole run. Three states matching the
 *  palette: failed (red), passed-but-unenforced (amber), passed (green), plus
 *  a muted no-verdict state. */
function RunVerdict({ stats }: { stats: RunStats }) {
  if (stats.tests === 0) return null;
  const kind = runVerdictKind(stats);
  if (kind === "failed") {
    return (
      <span className="run-verdict fail">
        ✗ Failed · {stats.failingTests} of {formatPlural(stats.tests, "test")}{" "}
        failing
      </span>
    );
  }
  if (kind === "xfailed") {
    return (
      <span
        className="run-verdict warn"
        title="All tests passed CI, but some are marked xfail: acknowledged (expected) failures."
      >
        Passed · {formatPlural(stats.xfailedTests, "expected failure")}
      </span>
    );
  }
  if (kind === "unenforced") {
    return (
      <span
        className="run-verdict warn"
        title="All tests passed, but some case verdicts failed without failing the build: the test handled the failure instead of letting it propagate."
      >
        Passed · {formatPlural(stats.unenforced, "case")} not enforced
      </span>
    );
  }
  if (kind === "none") {
    return (
      <span
        className="run-verdict muted"
        title="No test passed or failed. pytest skipped every test, so no eval recorded a result."
      >
        no verdict recorded
      </span>
    );
  }
  return <span className="run-verdict pass">✓ Passed</span>;
}

/** Quiet breakdown line under the verdict, totals plus case failures. */
function RunStatsLine({ stats }: { stats: RunStats }) {
  if (stats.tests === 0) return null;
  const parts = [
    formatPlural(stats.tests, "test"),
    formatPlural(stats.cases, "case"),
  ];
  if (stats.failingCases > 0) {
    parts.push(`${formatPlural(stats.failingCases, "case failure")}`);
  }
  return <div className="run-stats">{parts.join(" · ")}</div>;
}

const PROMOTED_LABEL_KEYS = new Set(["branch"]);

function RunHeader({
  run,
  via,
  refs,
  slug,
  mainline,
  prUrlTemplate,
  canCompareToBaseline,
  onCompareToBaseline,
  onDeleteRun,
  onDeleteRef,
}: {
  run: RunRecord;
  via?: string;
  refs: Ref[];
  slug: string;
  mainline?: MainlineEntry | null;
  prUrlTemplate: string | null;
  canCompareToBaseline: boolean;
  onCompareToBaseline: () => void;
  onDeleteRun: (slug: string, runId: string) => void;
  onDeleteRef: (slug: string, refName: string) => void;
}) {
  const branch = run.labels.branch;
  const otherLabels = Object.entries(run.labels).filter(
    ([k]) => !PROMOTED_LABEL_KEYS.has(k),
  );
  // The verdict is the reason anyone opens a run, so it gets the headline. A ref
  // keeps the title, because it names what the reader clicked, and the run id
  // moves to the meta line. A run with no tests has no verdict, so the id takes
  // the title.
  const stats = summariseRun(run);
  const title = via ?? (stats.tests === 0 ? run.id : null);
  // Deletable only when no loaded ref reaches it. Best effort, since refs
  // beyond the loaded page are unknown here; the server's 409 is the real
  // guard.
  const referenced = refs.length > 0;
  return (
    <header className="run-header">
      <div className="page-actions">
        <a
          className="page-action"
          href={api.runDownloadUrl(slug, run.id)}
          download={`${run.id}.json`}
          title="Download this run as JSON (includes the eval runner's own result objects)"
        >
          ↓ Download
        </a>
        {canCompareToBaseline && (
          <button
            type="button"
            className="page-action"
            title="Compare this run against the current mainline (the baseline ref)"
            onClick={onCompareToBaseline}
          >
            ⇄ Compare to mainline
          </button>
        )}
        {referenced ? (
          <button
            type="button"
            className="page-delete disabled"
            aria-disabled="true"
            title={`Referenced by ${refs.map((r) => r.name).join(", ")}. Delete the ref instead.`}
            onClick={(e) => e.preventDefault()}
          >
            Delete run
          </button>
        ) : (
          <button
            type="button"
            className="page-delete"
            title="Delete this run"
            onClick={() => onDeleteRun(slug, run.id)}
          >
            Delete run
          </button>
        )}
      </div>
      <p className="view-kicker">Eval run</p>
      <div className="run-title-row">
        {title !== null ? (
          <>
            <h1>{title}</h1>
            <RunVerdict stats={stats} />
          </>
        ) : (
          <h1 className="run-headline">
            <RunVerdict stats={stats} />
          </h1>
        )}
      </div>
      <RunStatsLine stats={stats} />
      {refs.length > 0 && (
        <div className="run-id-subtitle ref-chips">
          pointed at by:{" "}
          {refs.map((r) => (
            <span key={r.name} className="ref-chip">
              <code>{r.name}</code>
              {r.tip?.pr != null && (
                <>
                  {" · "}
                  <PrRef
                    pr={r.tip.pr}
                    title={r.tip.title}
                    template={prUrlTemplate}
                  />
                </>
              )}
              {r.name !== "baseline" && (
                <button
                  type="button"
                  className="ref-chip-delete"
                  title={`Delete ref ${r.name} and the runs only it reaches`}
                  aria-label={`Delete ref ${r.name}`}
                  onClick={() => onDeleteRef(slug, r.name)}
                >
                  ✕
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      <div className="run-meta">
        <span className="run-id-meta">
          run <code>{run.id}</code>
        </span>
        <span>{new Date(run.created_at).toLocaleString()}</span>
        {run.commit && (
          <span>
            commit <code>{run.commit.slice(0, 12)}</code>
            {run.worktree_dirty === true && (
              <span
                className="dirty-tag"
                title="working tree had uncommitted changes, so the run is not reproducible from this commit alone"
              >
                dirty
              </span>
            )}
          </span>
        )}
        {mainline?.commit && (
          <span
            className="run-mainline"
            title="Where this run landed on main when promoted. After a squash merge this differs from the eval-time commit above, which is no longer on main."
          >
            on main <code>{mainline.commit.slice(0, 12)}</code>
            {mainline.pr != null && (
              <>
                {" ("}
                <PrRef
                  pr={mainline.pr}
                  title={mainline.title}
                  template={prUrlTemplate}
                />
                {")"}
              </>
            )}
          </span>
        )}
        {branch && (
          <span>
            branch <code>{branch}</code>
          </span>
        )}
        <span title="Versions of the tools that recorded this run">
          evaltrack <code>{run.recorded_by.evaltrack}</code>
          {describeRunners(run).map((runner) => (
            <span key={runner}>
              {" · "}
              <code>{runner}</code>
            </span>
          ))}
        </span>
        {otherLabels.length > 0 && (
          <span className="labels">
            {otherLabels.map(([k, v]) => (
              <span key={k} className="label">
                {k}={v}
              </span>
            ))}
          </span>
        )}
      </div>
      <p className="hint">
        <span className="kbd">⌘/Ctrl</span>+click another run or ref in the
        sidebar to compare this run against it.
      </p>
    </header>
  );
}

/** Whether a click landed on something in the row that already handles it, such
 *  as a value cell, the attempt picker, an evaluator pill, or a note. A click
 *  anywhere else belongs to the row, which opens the case. */
function isOwnControl(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest("button, a, select, summary, input") !== null
  );
}

/** Key that names one case across its row, its drawer, and the shared attempt
 *  selection. */
function caseKeyOf(testLabel: string, name: string): string {
  return `${testLabel} · ${name}`;
}

/** The drawer payload for one case, built in one place so every way into a case
 *  lands in the same pane. `focus` is the part of it the click asked for. */
function caseDrawerContent(
  caseKey: string,
  c: CaseRecord,
  trends: Record<string, CaseTrend> | undefined,
  focus?: CaseFocus,
): DrawerContent {
  return {
    kind: "case",
    title: caseKey,
    caseKey,
    caseResult: c,
    trends,
    focus,
  };
}

function CaseTable({
  cases,
  totalCases,
  testLabel,
  testOutcome,
  reliabilityByCase,
  reliabilityLoading,
  trends,
  onOpenDrawer,
  attemptSel,
  onSelectAttempt,
}: {
  cases: [string, CaseRecord][];
  /** Cases the test recorded, so the caption can own up to the hidden rows. */
  totalCases: number;
  testLabel: string;
  testOutcome: TestOutcome | undefined;
  reliabilityByCase?: Record<string, CaseReliability>;
  reliabilityLoading?: boolean;
  /** This test's score history over the mainline, undefined while it loads and
   *  when the fetch failed. */
  trends?: ScoreHistory[];
  onOpenDrawer: (content: DrawerContent) => void;
  attemptSel: Record<string, number>;
  onSelectAttempt: (key: string, index: number) => void;
}) {
  // Show the reliability column while the data is loading (spinner), or once it
  // has pooled data to put in it (more than the open run, or a target to measure
  // against). Otherwise the column is dead weight for one-off runs.
  const showReliability =
    reliabilityLoading === true ||
    (reliabilityByCase !== undefined &&
      cases.some(([name]) => {
        const rel = reliabilityByCase[name];
        return rel != null && (rel.pooled_runs > 1 || rel.target != null);
      }));
  // Only show the Metadata column when some case carries `Case.metadata`, the
  // arbitrary per-case context the author attached. Empty otherwise.
  const showMetadata = cases.some(([, c]) => c.metadata != null);
  const showExpected = showsExpectedColumn(cases.map(([, c]) => c));
  return (
    <TableScroll>
      <table className="case-table">
        <caption className="case-table-caption">
          {cases.length === totalCases
            ? formatPlural(totalCases, "case")
            : `${cases.length} of ${formatPlural(totalCases, "case")}`}
        </caption>
        <thead>
          <tr>
            <th>Case</th>
            <th>Status</th>
            {showReliability && (
              <th title="Mainline reliability: the case's pooled pass-rate over recent runs on the mainline (not this run's score), so it reads the same whichever run you open. A dashboard signal that never fails a test.">
                Reliability
              </th>
            )}
            <th>Input</th>
            {showMetadata && (
              <th
                className="meta-col"
                title="Extra context the test attached to each case. Comes from Case.metadata."
              >
                Metadata
              </th>
            )}
            {showExpected && (
              <th title="What the case says the output should be. Comes from Case.expected_output. Open a cell to diff it against what the run produced.">
                Expected
              </th>
            )}
            <th>Output</th>
            <th>Assertions</th>
            <th>Scores</th>
            <th
              className="latency-col"
              title="Task latency (task_duration) of the shown attempt."
            >
              Latency
            </th>
          </tr>
        </thead>
        <tbody>
          {cases.map(([name, c]) => {
            // The row shows the selected attempt, the one picked in the
            // inspector (shared, keyed by this case's title), defaulting to the
            // deciding attempt. So selecting an attempt updates the row too.
            const caseKey = caseKeyOf(testLabel, name);
            const selIdx = attemptSel[caseKey] ?? findDecidingAttemptIndex(c);
            const attempts = c.attempts;
            const attempt = attempts[selIdx] ?? attempts[0] ?? null;
            const caseTrend = trends
              ? buildCaseTrends(trends, name)
              : undefined;
            // One inspector per case. Every cell in the row opens the same
            // pane, and `focus` is the part of it the click asked for.
            const caseContent = (focus?: CaseFocus): DrawerContent =>
              caseDrawerContent(caseKey, c, caseTrend, focus);
            const openCase = (focus?: CaseFocus) =>
              onOpenDrawer(caseContent(focus));
            return (
              <tr
                key={name}
                className="case-row"
                onClick={(e) => {
                  if (!isOwnControl(e.target)) openCase();
                }}
              >
                <td>
                  <button
                    type="button"
                    className="case-name"
                    title="Open this case"
                    onClick={() => openCase()}
                  >
                    {name}
                  </button>
                </td>
                <td>
                  <span
                    className={`pill ${OUTCOME_LABEL[c.outcome]}`}
                    title={
                      c.errored_attempts > 0
                        ? `${formatPlural(c.errored_attempts, "attempt")} errored: ` +
                          (attempts
                            .map((a) => summariseErrors(a.errors))
                            .find((line) => line !== null) ??
                            "the task or an evaluator raised")
                        : undefined
                    }
                  >
                    {OUTCOME_LABEL[c.outcome]}
                  </span>
                  {c.clean_attempts > 1 && (
                    <span
                      className={`flaky-chip ${flakyTone(c)}`}
                      title={
                        (formatAttemptCounts(c) ??
                          `${c.passed_attempts} of ${c.clean_attempts} attempts passed`) +
                        (recoveredOnARerun(c)
                          ? ", recovered on a later attempt"
                          : "")
                      }
                    >
                      {c.passed_attempts}/{c.clean_attempts}
                      {recoveredOnARerun(c) && (
                        <span className="flaky-recovered"> ↻</span>
                      )}
                    </span>
                  )}
                  {attempts.length > 1 && (
                    <AttemptPicker
                      c={c}
                      caseKey={caseKey}
                      selIdx={selIdx}
                      onSelect={onSelectAttempt}
                    />
                  )}
                  {c.outcome !== "passed" && testOutcome === "passed" && (
                    <span
                      className="pill not-enforced"
                      title="This case's verdict failed, but the test passed. The test handled the failure instead of letting it fail the build."
                    >
                      not enforced
                    </span>
                  )}
                </td>
                {showReliability && (
                  <td>
                    <ReliabilityCell
                      rel={reliabilityByCase?.[name]}
                      loading={reliabilityLoading}
                      errored={attempt?.outcome === "errored"}
                    />
                  </td>
                )}
                <td>
                  <CaseValue
                    value={c.inputs}
                    onOpenDrawer={onOpenDrawer}
                    content={caseContent({ section: "input" })}
                  />
                </td>
                {showMetadata && (
                  <td className="meta-cell meta-col">
                    <CaseValue
                      value={c.metadata}
                      onOpenDrawer={onOpenDrawer}
                      content={caseContent({ section: "metadata" })}
                      maxChars={14}
                    />
                  </td>
                )}
                {showExpected && (
                  <td>
                    {/* Sits beside the output on purpose, because the pair is
                        what a failed comparison is about. Clicking opens the
                        case at the section that holds both, diff first. */}
                    <CaseValue
                      value={c.expected_output}
                      onOpenDrawer={onOpenDrawer}
                      content={caseContent({ section: "expected" })}
                    />
                  </td>
                )}
                <td>
                  {/* An errored attempt shows its error here, because this
                      cell is where a reader looks for what the attempt
                      produced. */}
                  {attempt?.outcome === "errored" ? (
                    <CaseError
                      error={summariseErrors(attempt.errors)}
                      onOpenDrawer={onOpenDrawer}
                      content={caseContent({ section: "error" })}
                    />
                  ) : (
                    <CaseValue
                      value={attempt?.output}
                      onOpenDrawer={onOpenDrawer}
                      content={caseContent({ section: "output" })}
                    />
                  )}
                </td>
                <td>
                  {attemptShowsResults(attempt) ? (
                    <EvaluatorList
                      kind="assertion"
                      results={resultsOfKind(attempt.results, "assertion")}
                      onOpenCase={openCase}
                    />
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
                <td>
                  {attemptShowsResults(attempt) ? (
                    <EvaluatorList
                      kind="score"
                      results={resultsOfKind(attempt.results, "score")}
                      trends={caseTrend}
                      onOpenCase={openCase}
                    />
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
                <td className="latency-col">
                  {attempt ? (
                    <span
                      className="latency-cell"
                      title="Task latency for the shown attempt"
                    >
                      {attempt.task_duration === null
                        ? "—"
                        : formatDuration(attempt.task_duration)}
                    </span>
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

type ReliabilityChipState = "neutral" | "fail" | "ok";

/** How the rate stands against its target.
 *
 *  - `neutral`: no `reliability_target`, so there is nothing to pass or fail.
 *  - `fail`: the rate is under the target. The one state that alarms.
 *  - `ok`: the rate clears the target.
 *
 *  The 95% Wilson lower bound gets no state of its own. It would put a young
 *  eval in a third state for its first runs, which every reader has to learn
 *  before the chip means anything; the ⓘ note carries it instead.
 *
 *  Pure and exported, so tests check the thresholds rather than a rendered
 *  chip. */
export function reliabilityChipState(
  rel: CaseReliability,
): ReliabilityChipState {
  if (rel.target == null) return "neutral";
  return rel.below_target ? "fail" : "ok";
}

/** The mainline reliability for a case (the pooled pass-rate over the
 *  baseline history, not this run's score), with the viewed run drawn over the
 *  trend. When the viewed run's eval changed vs mainline, or the case is brand
 *  new, there's no mainline history to pool, so an explanation shows instead.
 *
 *  `errored` dims the figure. The shown attempt crashed and is not one of the
 *  pooled samples, so a healthy rate beside it misleads. */
function ReliabilityCell({
  rel,
  loading,
  errored,
}: {
  rel?: CaseReliability;
  loading?: boolean;
  errored?: boolean;
}) {
  if (loading)
    return (
      <span
        className="spinner"
        role="status"
        aria-label="loading reliability"
      />
    );
  if (!rel) return <span className="hint">—</span>;
  // The viewed run's eval_version differs from mainline, so its reliability
  // history starts fresh and there is nothing to pool yet.
  if (rel.diverged) {
    return (
      <ReliabilityNote label="eval changed">
        This eval's <code>eval_version</code> differs from mainline, so its
        history starts over: pass rates from the old version say nothing about
        this one. The estimate builds back up as runs with this version land on
        mainline.
      </ReliabilityNote>
    );
  }
  // No mainline samples, so the case exists only in the viewed run so far.
  if (rel.rate === null) {
    if (rel.points.length === 0) return <span className="hint">—</span>;
    return (
      <ReliabilityNote label="new">
        No mainline history for this case yet. It appears only in the run you're
        viewing. The reliability estimate builds up as runs land on mainline.
      </ReliabilityNote>
    );
  }
  // Rates render floored, so 199/200 never shows as "100" and the Wilson lower
  // bound is never rounded up past itself.
  const pct = formatRatePct(rel.rate);
  const target = rel.target != null ? Math.round(rel.target * 100) : null;
  const lbPct = rel.lower_bound != null ? formatRatePct(rel.lower_bound) : null;
  const state = reliabilityChipState(rel);
  const chipTitle =
    `Mainline reliability: ${rel.pooled_passes}/${rel.pooled_attempts} passing attempts over ${
      rel.pooled_runs
    } mainline run${rel.pooled_runs === 1 ? "" : "s"}` +
    (lbPct != null
      ? `. Conservative floor over these pooled_attempts: ${lbPct}%`
      : "") +
    (target != null
      ? `. Target ${target}%`
      : ". No reliability_target set for this eval, so the rate is history only");
  return (
    <div className={`reliability-cell${errored ? " errored" : ""}`}>
      <span
        className={`reliability-chip ${state}`}
        title={
          errored
            ? `${chipTitle}. The attempt shown for this run errored, so it is not one of these samples.`
            : chipTitle
        }
      >
        {pct}%
        {target != null && (
          <span className="reliability-target"> / {target}%</span>
        )}
      </span>
      <Popover
        className="reliability-info"
        summaryLabel="reliability sample size"
        summary={
          <span className="info-icon" aria-hidden="true">
            ⓘ
          </span>
        }
      >
        <>
          {buildReliabilityNoteLines(rel, SPARK_MAX_RUNS).map((line) => (
            <span className="note-line" key={line.label ?? line.text}>
              {line.label != null ? (
                <>
                  <strong>{line.label}:</strong> {line.text}
                </>
              ) : (
                line.text
              )}
            </span>
          ))}
        </>
      </Popover>
      <div className="reliability-spark-row">
        <Sparkline points={rel.points} />
      </div>
    </div>
  );
}

/** A click-to-open `<details>` whose panel is placed against the viewport.
 *
 *  The panel is `position: fixed`, so the scroll container of the case table
 *  can't clip it. With no offsets, a fixed box takes its position from the
 *  document origin, which puts it one scroll offset away from its icon. So the
 *  panel gets explicit coordinates from the summary when it opens, and keeps
 *  them current while it stays open.
 *
 *  It closes on Escape and on a click outside. The panel is the direct `<p>`
 *  child, so each caller keeps its own `.class > p` styling. */
function Popover({
  className,
  summary,
  summaryLabel,
  children,
}: {
  className: string;
  summary: ReactNode;
  summaryLabel: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);

  const place = useCallback(() => {
    const details = ref.current;
    const trigger = details?.querySelector<HTMLElement>(":scope > summary");
    const panel = details?.querySelector<HTMLElement>(":scope > p");
    if (!trigger || !panel) return;
    const anchor = trigger.getBoundingClientRect();
    // Clamped to the viewport so a cell at the right edge (or a narrow window)
    // keeps the whole panel on screen.
    const left = Math.min(
      Math.max(8, anchor.left),
      Math.max(8, window.innerWidth - panel.offsetWidth - 8),
    );
    panel.style.top = `${anchor.bottom + 6}px`;
    panel.style.left = `${left}px`;
  }, []);

  useEffect(() => {
    if (!open) return;
    place();
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    // Capture phase, because the scroll that moves the anchor usually happens
    // in an inner container and does not bubble to the window.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open, place]);

  return (
    <details
      ref={ref}
      className={className}
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary aria-label={summaryLabel}>{summary}</summary>
      <p>{children}</p>
    </details>
  );
}

/** The "eval changed" / "new" reliability states. A click-to-expand explanation
 *  (native `<details>`) rather than a hover tooltip, so it is discoverable and
 *  works on touch. */
function ReliabilityNote({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <details className="reliability-note">
      <summary>
        <span className="info-icon" aria-hidden="true">
          ⓘ
        </span>{" "}
        {label}
      </summary>
      <p>{children}</p>
    </details>
  );
}

// The sparkline shows at most this many of the most recent runs (newest on the
// right). More than this and the strip dominates the table without adding
// legible signal. The chip and the ⓘ note carry the full pooled totals.
const SPARK_MAX_RUNS = 20;
// Backstop width in px. Even within the run cap, reruns can multiply the bars,
// so a wider strip scales down to this via the SVG viewBox rather than blowing
// out the column.
const SPARK_MAX_W = 200;

const SPARK_BAR_W = 4;
const SPARK_GAP = 1; // between attempts within a run
const SPARK_RUN_GAP = 4; // between runs
// Wider still before the viewed run, so the overlay shows as a separate group
// rather than as one more mainline run.
const SPARK_OVERLAY_GAP = 9;

interface SparkBar {
  x: number;
  passed: boolean;
  commit: string | null;
  index: number;
  runId: string;
  /** The viewed run's own attempts, drawn but never part of the pooled rate. */
  off_mainline: boolean;
}

interface SparkLayout {
  bars: SparkBar[];
  width: number;
  /** Mainline runs shown, and how many the case has in the pooled window. */
  shown: number;
  mainlineRuns: number;
  hasOverlay: boolean;
}

/** Place one bar per clean attempt, left to right, oldest to newest.
 *
 *  Mainline runs come first, capped to the most recent `SPARK_MAX_RUNS` so the
 *  strip can't outgrow the column. The viewed run's attempts follow after a
 *  wider gap, unless that run is itself a mainline run. The gaps carry the
 *  grouping. A small gap separates the attempts of one run, a wider one
 *  separates runs, and the widest marks the overlay.
 *
 *  Pure, so the geometry and the mainline split are testable without a DOM. */
export function layoutSparkline(points: ReliabilityPoint[]): SparkLayout {
  const mainline = points.filter((p) => !p.off_mainline);
  const overlay = points.filter((p) => p.off_mainline);
  // Newest on the right. Runs are oldest→newest, so the last N are the most
  // recent and stay anchored at the right edge.
  const shown = mainline.slice(-SPARK_MAX_RUNS);
  const bars: SparkBar[] = [];
  let x = 0;
  const place = (p: ReliabilityPoint, gapAfter: number) => {
    p.attempts.forEach((passed, index) => {
      bars.push({
        x,
        passed,
        commit: p.commit,
        index,
        runId: p.run_id,
        off_mainline: p.off_mainline,
      });
      x += SPARK_BAR_W + SPARK_GAP;
    });
    x += gapAfter - SPARK_GAP; // swap the trailing inner gap for a group gap
  };
  shown.forEach((p, i) =>
    place(
      p,
      i === shown.length - 1 && overlay.length > 0
        ? SPARK_OVERLAY_GAP
        : SPARK_RUN_GAP,
    ),
  );
  overlay.forEach((p) => place(p, SPARK_RUN_GAP));
  const last = bars.at(-1);
  return {
    bars,
    width: last ? last.x + SPARK_BAR_W : 0,
    shown: shown.length,
    mainlineRuns: mainline.length,
    hasOverlay: overlay.length > 0,
  };
}

/** A small sparkline of per-attempt outcomes, oldest to newest. One bar per
 *  clean attempt, green for a pass and red for a fail, grouped by run. A first
 *  attempt that failed and was rescued by reruns shows straight off the bars.
 *
 *  The bars left of the wide gap are the mainline history that the rate pools.
 *  The outlined bars right of it are the run being viewed. Drawing it shows a
 *  run that diverges from its own history, and it never counts toward the
 *  rate. */
function Sparkline({ points }: { points: ReliabilityPoint[] }) {
  const { bars, width, shown, mainlineRuns, hasOverlay } =
    layoutSparkline(points);
  // A single bar shows nothing useful. The chip already carries the number.
  if (bars.length < 2) return null;
  const h = 16;
  // Older runs fall off the left edge (newest is on the right). When some are
  // hidden, fade that edge so the strip shows that history continues beyond it.
  const truncated = mainlineRuns > shown;
  const label =
    (truncated
      ? `per-attempt mainline outcomes, ${shown} most recent of ${mainlineRuns} runs`
      : "per-attempt mainline outcomes") +
    (hasOverlay ? ", then the run being viewed" : "");
  return (
    <svg
      className={`reliability-spark${truncated ? " truncated" : ""}`}
      width={width}
      height={h}
      viewBox={`0 0 ${width} ${h}`}
      preserveAspectRatio="none"
      style={{ maxWidth: SPARK_MAX_W }}
      role="img"
      aria-label={label}
    >
      {bars.map((b, i) => (
        <rect
          // Keyed by place in the strip rather than by run, because a run can
          // hold more than one place in a history it entered twice. Bars are
          // stateless and are laid out afresh from the points, so position is a
          // stable identity.
          key={i}
          x={b.off_mainline ? b.x + 0.5 : b.x}
          y={b.off_mainline ? 0.5 : 0}
          // Inset by half the stroke, so an outlined bar's border stays inside
          // the viewBox instead of being clipped at the top and bottom edges.
          width={b.off_mainline ? SPARK_BAR_W - 1 : SPARK_BAR_W}
          height={b.off_mainline ? h - 1 : h}
          className={
            `spark-bar ${b.passed ? "consistent" : "fail"}` +
            (b.off_mainline ? " overlay" : "")
          }
        >
          <title>
            {b.off_mainline
              ? `this run (not pooled): attempt ${b.index + 1} ${
                  b.passed ? "passed" : "failed"
                }`
              : (b.commit ? `${b.commit.slice(0, 8)}: ` : "") +
                `attempt ${b.index + 1}${b.index === 0 ? " (first)" : ""} ` +
                (b.passed ? "passed" : "failed")}
          </title>
        </rect>
      ))}
    </svg>
  );
}

/** Tone for the per-case flakiness chip. All attempts passing is calm. A mix is
 *  the flaky case, amber regardless of the final verdict. Zero passing is a hard
 *  fail. */
function flakyTone(c: CaseRecord): string {
  if (c.passed_attempts === c.clean_attempts) return "consistent";
  if (c.passed_attempts === 0) return "fail";
  return "warn";
}

// Above this many attempts the inline tick strip overflows the row, so the
// picker switches to a compact dropdown that stays one width at any count.
const ATTEMPT_STRIP_MAX = 10;

/** Inline attempt selector in the row. A clickable tick strip for a few
 *  attempts, showing the pass/fail pattern, and a compact dropdown for many.
 *  Writes the shared selection, so the row's cells and the field panes follow. */
function AttemptPicker({
  c,
  caseKey,
  selIdx,
  onSelect,
}: {
  c: CaseRecord;
  caseKey: string;
  selIdx: number;
  onSelect: (key: string, index: number) => void;
}) {
  const attempts = c.attempts;
  if (attempts.length <= ATTEMPT_STRIP_MAX) {
    return (
      <span className="attempt-ticks" role="tablist" aria-label="attempts">
        {attempts.map((a, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === selIdx}
            className={`attempt-tick-sm ${OUTCOME_LABEL[a.outcome]}${
              i === selIdx ? " active" : ""
            }`}
            title={`attempt ${i + 1}: ${
              OUTCOME_LABEL[a.outcome]
            }. Show this attempt.`}
            onClick={() => onSelect(caseKey, i)}
          >
            {OUTCOME_MARK[a.outcome]}
          </button>
        ))}
      </span>
    );
  }
  return (
    <select
      className="attempt-select"
      aria-label="select attempt"
      value={selIdx}
      onChange={(e) => onSelect(caseKey, Number(e.target.value))}
    >
      {attempts.map((a, i) => (
        <option key={i} value={i}>
          attempt {i + 1} {OUTCOME_MARK[a.outcome]}
        </option>
      ))}
    </select>
  );
}

/** The output cell for an attempt that errored. Shows the exception line,
 *  truncated, and opens the same side panel as a value does. Warn-toned,
 *  because an error is not a failed assertion. */
function CaseError({
  error,
  onOpenDrawer,
  content,
}: {
  error?: string | null;
  onOpenDrawer: (content: DrawerContent) => void;
  content: DrawerContent;
}) {
  return (
    <button
      type="button"
      className="value-compact case-error"
      title={error ?? "The task or an evaluator raised."}
      onClick={() => onOpenDrawer(content)}
    >
      ⚠ {truncate(error ?? "errored", 24)}
    </button>
  );
}

function CaseValue({
  value,
  onOpenDrawer,
  content,
  maxChars = 27,
}: {
  value: unknown;
  onOpenDrawer: (content: DrawerContent) => void;
  /** The case pane to open on click, at the section that this cell shows. */
  content: DrawerContent;
  /** Truncation length for the compact cell. Default 27 (≈ the 28ch cap).
   *  Secondary cells (e.g. metadata) pass a smaller value to look lighter. */
  maxChars?: number;
}) {
  if (value === null || value === undefined) {
    return <span className="json-null">—</span>;
  }
  // The truncated value is itself the control. Activating it opens the full
  // (collapsible, syntax-coloured) view in the side panel. A button rather than
  // a styled `code`, so it is reachable by keyboard and announced as a control.
  // Otherwise the preview is all a non-mouse user can ever see of the value.
  return (
    <button
      type="button"
      className="value-compact"
      title="open in side panel"
      onClick={() => onOpenDrawer(content)}
    >
      {truncate(formatDisplayText(value), maxChars)}
    </button>
  );
}
