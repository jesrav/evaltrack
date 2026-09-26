import { useMemo } from "react";
import { scoreClearsBar } from "../checks";
import { readDeferredEnvelope } from "../deferred";
import { formatDisplayText, getPrimaryView, truncate } from "../extract";
import { formatBytes } from "../format";
import {
  diffRuns,
  hasDiffChanges,
  summariseDiffModule,
  summariseTestDiff,
  type CaseStatus,
  type CaseRow,
  type DiffSummary,
  type VerdictChange,
} from "../diff";
import { groupNodeidsByModule } from "../grouping";
import {
  formatDuration,
  formatDurationDelta,
  formatScore,
  pickNoun,
  formatPlural,
} from "../format";
import type {
  RunRecord,
  EvaluatorResult,
  TestOutcome,
  RecordedTest,
} from "../types";
import type { TestDiff, RunDiff as RunDiffData } from "../diff";
import { isContainer } from "./JsonView";
import { summarizeChange } from "./JsonDiff";
import { TableScroll } from "./TableScroll";
import type { DrawerContent } from "./Drawer";
import { numericValue } from "../results";

interface Props {
  a: RunRecord;
  b: RunRecord;
  viaA?: string;
  viaB?: string;
  onSwap: () => void;
  onOpenDrawer: (content: DrawerContent) => void;
}

/** One test's marker score bars, kept apart per side of the diff. */
export function RunDiff({ a, b, viaA, viaB, onSwap, onOpenDrawer }: Props) {
  const diff = useMemo(() => diffRuns(a, b), [a, b]);

  // Merge the two runs' test records so grouping can resolve a module path for
  // every diffed test. A's record wins for a shared test. Only `test_file`
  // is read here, which matches across sides for a shared test.
  const mergedTests = useMemo(
    (): Record<string, RecordedTest> => ({ ...b.tests, ...a.tests }),
    [a.tests, b.tests],
  );

  const groups = useMemo(
    () =>
      groupNodeidsByModule(
        diff.tests.map((t) => t.nodeid),
        mergedTests,
      ),
    [diff.tests, mergedTests],
  );

  const totals = diff.totals;
  const labelA = a.id.slice(0, 10);
  const labelB = b.id.slice(0, 10);
  const testByNodeid = useMemo(
    () => new Map(diff.tests.map((t) => [t.nodeid, t])),
    [diff.tests],
  );

  const testChanges = useMemo(() => testLevelChanges(diff, a, b), [diff, a, b]);

  return (
    <div>
      <header className="view-title">
        <h1>Eval run comparison</h1>
        <p className="hint">
          Showing what changed from base (left) to compare (right). Pick a
          single run in the sidebar to leave this view.
        </p>
      </header>
      <div className="run-pickers">
        <RunPickerCard role="base" run={a} label={labelA} via={viaA} />
        <button className="swap-btn" type="button" onClick={onSwap}>
          ⇄ swap
        </button>
        <RunPickerCard role="compare" run={b} label={labelB} via={viaB} />
      </div>

      <DiffStatBar
        totals={totals}
        onlyBase={totals.casesOnlyInA + testChanges.removed}
        onlyCompare={totals.casesOnlyInB + testChanges.added}
      />

      {testChanges.items.length > 0 && (
        <details open={testChanges.items.length < 5}>
          <summary>
            Test-level changes ({testChanges.removed} removed,{" "}
            {testChanges.added} added)
          </summary>
          <ul style={{ marginTop: 8 }}>
            {testChanges.items.map((c) => (
              <li key={c.key}>
                <span className="pill missing">{c.label}</span>{" "}
                <code>{c.name}</code>
              </li>
            ))}
          </ul>
        </details>
      )}

      {groups.map((group) => {
        const moduleSummary = summariseDiffModule(group.tests, testByNodeid);
        return (
          <details
            className="module-block"
            key={group.module}
            open={hasDiffChanges(moduleSummary) || groups.length === 1}
          >
            <summary className="module-header">
              <span className="module-caret" aria-hidden="true">
                ▸
              </span>
              <span className="module-path">{group.module}</span>
              <DiffModuleStatusBadge summary={moduleSummary} />
            </summary>
            {group.tests.map((nodeid) => {
              const test = testByNodeid.get(nodeid);
              if (!test) return null;
              const testSummary = summariseTestDiff(test);
              return (
                <details
                  className="report-block"
                  key={nodeid}
                  open={hasDiffChanges(testSummary)}
                >
                  <summary className="report-header">
                    <span className="report-caret">▸</span>
                    <span className="report-name">{nodeid}</span>
                    <VerdictBadge change={test.verdictChange} />
                    <OutcomeChangeBadge test={test} />
                    <span className="hint">
                      {test.newlyFailing} newly failing · {test.newlyPassing}{" "}
                      newly passing · {test.stillPassing} still passing
                      {test.errored > 0 ? ` · ${test.errored} errored` : ""}
                      {test.stillFailing > 0
                        ? ` · ${test.stillFailing} still failing`
                        : ""}
                      {test.scoresRegressed > 0
                        ? ` · ${formatPlural(
                            test.scoresRegressed,
                            "case",
                          )} with a lower score`
                        : ""}
                      {test.scoresImproved > 0
                        ? ` · ${formatPlural(
                            test.scoresImproved,
                            "case",
                          )} with a higher score`
                        : ""}
                      {testSummary.contentChanged > 0
                        ? ` · ${formatPlural(
                            testSummary.contentChanged,
                            "case",
                          )} changed`
                        : ""}
                      {test.onlyInA + test.onlyInB > 0
                        ? ` · ${test.onlyInA + test.onlyInB} only on one side`
                        : ""}
                    </span>
                  </summary>
                  {test.cases.length > 0 ? (
                    <CaseDiffTable
                      cases={test.cases}
                      testLabel={nodeid}
                      labelA={labelA}
                      labelB={labelB}
                      onOpenDrawer={onOpenDrawer}
                    />
                  ) : (
                    <p className="hint">
                      Neither run recorded a case for this test.
                    </p>
                  )}
                </details>
              );
            })}
          </details>
        );
      })}
    </div>
  );
}

export interface TestLevelChanges {
  /** One row per test present in only one run, BASE's first. */
  items: { key: string; name: string; label: string }[];
  /** Tests absent from COMPARE, and tests absent from BASE. */
  removed: number;
  added: number;
}

/** The tests only one run has. One that errored or was skipped there before
 *  evaluating recorded no cases, so its outcome is the only clue to what
 *  happened and is shown in parentheses. */
export function testLevelChanges(
  diff: RunDiffData,
  a: RunRecord,
  b: RunRecord,
): TestLevelChanges {
  const out: TestLevelChanges = { items: [], removed: 0, added: 0 };
  const add = (name: string, side: "BASE" | "COMPARE") => {
    const here = (side === "BASE" ? a : b).tests[name];
    if (side === "BASE") out.removed += 1;
    else out.added += 1;
    let label = `only in ${side}`;
    if (here?.marker == null && here?.outcome) label += ` (${here.outcome})`;
    out.items.push({ key: `${side}-${name}`, name, label });
  };
  for (const name of diff.testsOnlyInA) add(name, "BASE");
  for (const name of diff.testsOnlyInB) add(name, "COMPARE");
  return out;
}

function VerdictBadge({ change }: { change: VerdictChange }) {
  if (change === "regressed") {
    return <span className="pill fail">regressed</span>;
  }
  if (change === "recovered") {
    return <span className="pill pass">recovered</span>;
  }
  return null;
}

// The test's pytest outcome changed between runs, surfaced even when every case
// verdict is identical (e.g. an xfail marker added or removed). Coloured by
// where it landed. Failed or errored is bad, passed is good.
function OutcomeChangeBadge({ test }: { test: TestDiff }) {
  if (!test.outcomeChanged) return null;
  const to = test.outcomeB;
  const tone = to === "passed" ? "pass" : to === null ? "missing" : "fail";
  return (
    <span
      className={`pill ${tone}`}
      title="The test's pytest outcome changed between the two runs."
    >
      test {outcomeText(test.outcomeA)} → {outcomeText(test.outcomeB)}
    </span>
  );
}

function outcomeText(o: TestOutcome | null): string {
  return o ?? "—";
}

function DiffModuleStatusBadge({ summary }: { summary: DiffSummary }) {
  const {
    regressed,
    recovered,
    outcomeFlips,
    newlyFailing,
    newlyPassing,
    errored,
    contentChanged,
    stillPassing,
    stillFailing,
    scoresRegressed,
    scoresImproved,
    onlyInEither,
  } = summary;
  // Content moving under held verdicts is worth opening, but nothing regressed,
  // so it reads in the same muted tone as no changes at all.
  if (!hasDiffChanges({ ...summary, contentChanged: 0 })) {
    return (
      <span className="module-status muted">
        {contentChanged > 0
          ? `${formatPlural(contentChanged, "case")} changed`
          : "no changes"}
        {stillPassing > 0 ? ` · ${stillPassing} still passing` : ""}
      </span>
    );
  }
  // Still-failing and dropped scores colour the badge too. A module red on both
  // sides has no flips, and showing "pass" or "warn" is a lie.
  const tone =
    regressed > 0 || newlyFailing > 0 || stillFailing > 0
      ? "fail"
      : newlyPassing > 0 || recovered > 0
        ? "pass"
        : outcomeFlips > 0 ||
            errored > 0 ||
            scoresRegressed > 0 ||
            onlyInEither > 0
          ? "warn"
          : "pass";
  const parts: string[] = [];
  if (regressed > 0) parts.push(`${regressed} regressed`);
  if (recovered > 0) parts.push(`${recovered} recovered`);
  if (outcomeFlips > 0) parts.push(formatPlural(outcomeFlips, "outcome flip"));
  if (newlyFailing > 0) parts.push(`${newlyFailing} newly failing`);
  if (newlyPassing > 0) parts.push(`${newlyPassing} newly passing`);
  if (errored > 0) parts.push(`${errored} errored`);
  if (stillFailing > 0) parts.push(`${stillFailing} still failing`);
  // Counted per case rather than per score, so a case whose scores both dropped
  // is one.
  if (scoresRegressed > 0)
    parts.push(formatPlural(scoresRegressed, "case") + " with a lower score");
  if (scoresImproved > 0)
    parts.push(formatPlural(scoresImproved, "case") + " with a higher score");
  if (onlyInEither > 0) parts.push(`${onlyInEither} only on one side`);
  return <span className={`module-status ${tone}`}>{parts.join(" · ")}</span>;
}

function RunPickerCard({
  role,
  run,
  label,
  via,
}: {
  role: "base" | "compare";
  run: RunRecord;
  label: string;
  via: string | undefined;
}) {
  const branch = run.labels.branch;
  const primary = via ?? label;
  return (
    <div className="run-picker">
      <div className="picker-heading">
        <span className="picker-primary">{primary}</span>
        <span className={`picker-role role-${role}`}>{role}</span>
      </div>
      <code className="picker-id">{run.id}</code>
      <span className="hint">
        {run.commit ? `commit ${run.commit.slice(0, 8)}` : "no commit"}
        {run.worktree_dirty === true && (
          <span
            className="dirty-tag"
            title="working tree had uncommitted changes"
          >
            dirty
          </span>
        )}{" "}
        · {new Date(run.created_at).toLocaleString()}
      </span>
      {branch && (
        <span className="hint">
          branch <code>{branch}</code>
        </span>
      )}
    </div>
  );
}

// One line of counts. Only non-zero figures render, each tone-coloured on its
// number. "Still passing" is demoted to the end, and the explanatory note lives
// in a tooltip on a trailing ⓘ. Still-failing cases (red) and dropped scores
// (amber) surface here too, because without them a run that is worse on both
// sides shows nothing but the muted tail. A dropped score that still clears its
// bar warns rather than fails.
function DiffStatBar({
  totals,
  onlyBase,
  onlyCompare,
}: {
  totals: RunDiffData["totals"];
  onlyBase: number;
  onlyCompare: number;
}) {
  const items: {
    key: string;
    label: string;
    value: number;
    tone?: "good" | "bad" | "warn";
  }[] = [
    {
      key: "rr",
      label: `${pickNoun(totals.testsRegressed, "test", "tests")} regressed`,
      value: totals.testsRegressed,
      tone: "bad",
    },
    {
      key: "rc",
      label: `${pickNoun(totals.testsRecovered, "test", "tests")} recovered`,
      value: totals.testsRecovered,
      tone: "good",
    },
    {
      key: "of",
      label: pickNoun(totals.outcomeFlips, "outcome flip", "outcome flips"),
      value: totals.outcomeFlips,
      tone: "warn",
    },
    {
      key: "nf",
      label: `${pickNoun(totals.newlyFailing, "case", "cases")} newly failing`,
      value: totals.newlyFailing,
      tone: "bad",
    },
    {
      key: "np",
      label: `${pickNoun(totals.newlyPassing, "case", "cases")} newly passing`,
      value: totals.newlyPassing,
      tone: "good",
    },
    {
      key: "er",
      // Amber, not red. An errored case says nothing about output quality,
      // and CI is already red through the test outcome.
      label: `${pickNoun(totals.casesErrored, "case", "cases")} errored (no verdict)`,
      value: totals.casesErrored,
      tone: "warn",
    },
    {
      key: "sf",
      label: `${pickNoun(totals.stillFailing, "case", "cases")} still failing`,
      value: totals.stillFailing,
      tone: "bad",
    },
    {
      key: "sd",
      label: `${pickNoun(totals.scoresRegressed, "case", "cases")} with a lower score`,
      value: totals.scoresRegressed,
      // A dropped score that still clears its bar is a heads-up, not a gate
      // failure, so it warns rather than turns red. Matches the module badge,
      // which likewise never turns `fail` on a score drop alone.
      tone: "warn",
    },
    { key: "ob", label: "only in BASE", value: onlyBase, tone: "warn" },
    { key: "oc", label: "only in COMPARE", value: onlyCompare, tone: "warn" },
  ];
  const active = items.filter((i) => i.value > 0);
  return (
    <div className="diff-stats">
      {active.length === 0 ? (
        <span className="diff-stat">No verdict changes</span>
      ) : (
        active.map((i) => (
          <span
            key={i.key}
            className={`diff-stat${i.tone ? " " + i.tone : ""}`}
          >
            <strong className="diff-stat-num">{i.value}</strong> {i.label}
          </span>
        ))
      )}
      <span className="diff-stat muted">
        <strong className="diff-stat-num">{totals.stillPassing}</strong> still
        passing
      </span>
      <details className="diff-stats-info">
        <summary aria-label="What these counts mean">
          <span className="info-icon" aria-hidden="true">
            ⓘ
          </span>
        </summary>
        <p>
          "Tests regressed/recovered" count whole tests with at least one case
          whose pass/fail verdict flipped. The case counts track individual
          per-case flips. "Errored" are cases where a side's task or evaluator
          raised. That side reached no verdict, so the case is neither a flip
          nor a hold. "Still failing" are cases that failed on both sides, so
          nothing flipped and nothing was fixed. "With a lower score" are cases
          whose verdict held while some score went down. "Only in BASE/COMPARE"
          are cases or tests present on only one side.
        </p>
      </details>
    </div>
  );
}

function CaseDiffTable({
  cases,
  testLabel,
  labelA,
  labelB,
  onOpenDrawer,
}: {
  cases: CaseRow[];
  testLabel: string;
  labelA: string;
  labelB: string;
  onOpenDrawer: (content: DrawerContent) => void;
}) {
  return (
    <TableScroll>
      <table className="case-table">
        <thead>
          <tr>
            <th>Case</th>
            <th>BASE</th>
            <th>→</th>
            <th>COMPARE</th>
            <th>Input</th>
            <th>Output</th>
            <th>Assertions BASE → COMPARE</th>
            <th>Scores BASE → COMPARE</th>
            <th className="latency-col">Latency</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((row) => {
            // A flaky case has >1 attempt on at least one side and exists on both,
            // so an attempt comparison is meaningful.
            const caseKey = `${testLabel} · ${row.caseId}`;
            const flaky =
              row.resultA != null &&
              row.resultB != null &&
              (row.cleanAttemptsA > 1 || row.cleanAttemptsB > 1);
            const subrunContent: DrawerContent | undefined =
              flaky && row.resultA && row.resultB
                ? {
                    kind: "subrundiff",
                    title: `${caseKey} · output`,
                    caseKey,
                    resultA: row.resultA,
                    resultB: row.resultB,
                    labelA,
                    labelB,
                    field: { type: "output" },
                  }
                : undefined;
            return (
              <tr key={row.caseId} className={rowChangeClass(row)}>
                <td>{row.caseId}</td>
                <td>
                  <StatusPill s={row.a} />
                  {row.cleanAttemptsA > 1 && (
                    <RatePill
                      passes={row.passesA}
                      cleanAttempts={row.cleanAttemptsA}
                    />
                  )}
                </td>
                <td className="diff-arrow">→</td>
                <td>
                  <StatusPill s={row.b} />
                  {row.cleanAttemptsB > 1 && (
                    <RatePill
                      passes={row.passesB}
                      cleanAttempts={row.cleanAttemptsB}
                    />
                  )}
                </td>
                <td>
                  <DiffCell
                    a={row.inputA}
                    b={row.inputB}
                    changed={row.inputChanged}
                    title={`${testLabel} · ${row.caseId} · input`}
                    labelA={labelA}
                    labelB={labelB}
                    onOpenDrawer={onOpenDrawer}
                  />
                </td>
                <td>
                  <DiffCell
                    a={row.outputA}
                    b={row.outputB}
                    changed={row.outputChanged}
                    title={`${testLabel} · ${row.caseId} · output`}
                    labelA={labelA}
                    labelB={labelB}
                    onOpenDrawer={onOpenDrawer}
                    content={subrunContent}
                  />
                </td>
                <td>
                  <ValueDiffList
                    kind="assertion"
                    row={row}
                    testLabel={testLabel}
                    labelA={labelA}
                    labelB={labelB}
                    onOpenDrawer={onOpenDrawer}
                  />
                </td>
                <td>
                  <ScoreDiffList
                    row={row}
                    testLabel={testLabel}
                    labelA={labelA}
                    labelB={labelB}
                    onOpenDrawer={onOpenDrawer}
                  />
                </td>
                <td className="latency-col">
                  <LatencyDiffCell row={row} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

/** The value an unchanged cell shows. A case added or removed inside a test
 *  exists on one side only, and that lone side is what recorded a value. */
export function unchangedCellValue(a: unknown, b: unknown): unknown {
  return a !== undefined ? a : b;
}

function DiffCell({
  a,
  b,
  changed,
  title,
  labelA,
  labelB,
  onOpenDrawer,
  content,
}: {
  a: unknown;
  b: unknown;
  changed: boolean;
  title: string;
  labelA: string;
  labelB: string;
  onOpenDrawer: (content: DrawerContent) => void;
  /** Drawer to open on click. Defaults to a plain two-value diff. Set for flaky
   *  cases, where it opens the attempt comparison. */
  content?: DrawerContent;
}) {
  const open = () =>
    onOpenDrawer(
      content ?? {
        kind: "diff",
        title,
        subtitleA: labelA,
        subtitleB: labelB,
        a,
        b,
      },
    );

  if (!changed) {
    const display = formatDisplayText(unchangedCellValue(a, b));
    return (
      <div className="cell-value">
        <button
          type="button"
          className="value-compact"
          title="open in side panel"
          onClick={open}
        >
          {/* 27 ≈ the .value-compact 28ch cap, so the trailing … is the
              visible truncation marker (CSS only clips as a safety net). */}
          {truncate(display, 27)}
        </button>
      </div>
    );
  }

  // Summarize the *primary* view, the same unwrapped projection the change
  // decision and inline text use, so a wrapped result (e.g. an AgentRunResult)
  // reports what changed in the result rather than in its `_state` or usage
  // metadata. For structured values, name what changed (e.g. "is_question:
  // true → false") instead of a char count. Keep the char delta for plain
  // strings.
  // A deferred value is not loaded, so the label shows its size.
  const da = readDeferredEnvelope(a);
  const db = readDeferredEnvelope(b);
  const pa = getPrimaryView(a);
  const pb = getPrimaryView(b);
  const label =
    da || db
      ? `changed · ${formatBytes(da?.size ?? formatDisplayText(a).length)} → ${formatBytes(db?.size ?? formatDisplayText(b).length)}`
      : isContainer(pa) || isContainer(pb)
        ? truncate(summarizeChange(pa, pb), 48)
        : `changed · ${formatDisplayText(a).length} → ${formatDisplayText(b).length} chars`;
  return (
    <div className="cell-value">
      <button
        className="diff-pill"
        type="button"
        title="open diff in side panel"
        onClick={open}
      >
        {label}
      </button>
    </div>
  );
}

function StatusPill({ s }: { s: CaseStatus }) {
  if (s === "missing") return <span className="pill missing">missing</span>;
  if (s === "errored") return <span className="pill errored">errored</span>;
  if (s === "failing") return <span className="pill fail">fail</span>;
  return <span className="pill pass">pass</span>;
}

/** Deciding-attempt latency per side, with the signed delta shown only when it
 *  moved by a meaningful margin (see `latencyChanged`). Neutral by design,
 *  because slower is not a regression, so it gets no pass/fail colour. */
function LatencyDiffCell({ row }: { row: CaseRow }) {
  if (row.durationA == null && row.durationB == null) {
    return <span className="json-null">—</span>;
  }
  const delta =
    row.durationA != null && row.durationB != null
      ? row.durationB - row.durationA
      : null;
  return (
    <span className={`latency-diff${row.durationChanged ? " changed" : ""}`}>
      {row.durationA != null ? formatDuration(row.durationA) : "—"}
      {" → "}
      {row.durationB != null ? formatDuration(row.durationB) : "—"}
      {delta != null && row.durationChanged && (
        <span className="latency-delta"> ({formatDurationDelta(delta)})</span>
      )}
    </span>
  );
}

/** Per-side pass-rate for a flaky case in the diff. Tone follows the flakiness
 *  rather than the verdict. All-pass is calm, none-pass is a hard fail, a mix is
 *  amber. */
function RatePill({
  passes,
  cleanAttempts,
}: {
  passes: number;
  cleanAttempts: number;
}) {
  const tone =
    passes === cleanAttempts ? "consistent" : passes === 0 ? "fail" : "warn";
  return (
    <span
      className={`flaky-chip ${tone}`}
      title={`${passes} of ${cleanAttempts} attempts passed`}
    >
      {passes}/{cleanAttempts}
    </span>
  );
}

// Tier a changed row by severity so a regression does not look like a benign
// output drift. Verdict flips dominate, then added or removed, then a content
// change where the verdict held.
function rowChangeClass(row: CaseRow): string {
  if (row.a === "passing" && row.b === "failing") return "row-regressed";
  if (row.a === "failing" && row.b === "passing") return "row-recovered";
  if (row.a === "missing" || row.b === "missing") return "row-onlyside";
  if (row.changed) return "row-drifted";
  return "";
}

interface DiffListProps {
  row: CaseRow;
  testLabel: string;
  labelA: string;
  labelB: string;
  onOpenDrawer: (content: DrawerContent) => void;
}

// Open both sides' full evaluator outcome (value + reason + evaluator.arguments)
// side by side in the drawer. A red/green diff of free-form reasons is noise.
// Showing both JSONs is clearer.
function openOutcomePair(
  props: DiffListProps,
  name: string,
  a: unknown,
  b: unknown,
): void {
  props.onOpenDrawer({
    kind: "pair",
    title: `${props.testLabel} · ${props.row.caseId} · ${name}`,
    labelA: props.labelA,
    labelB: props.labelB,
    a: a ?? null,
    b: b ?? null,
  });
}

function isFlakyRow(row: CaseRow): boolean {
  return (
    row.resultA != null &&
    row.resultB != null &&
    (row.cleanAttemptsA > 1 || row.cleanAttemptsB > 1)
  );
}

// Open a named assertion or score across attempts (two BASE/COMPARE strips)
// when the case is flaky. Otherwise open the plain both-sides result pair on the
// deciding attempts. The caseKey matches the output cell's, so the attempt
// selection is shared across all of a case's fields.
function openEvaluator(
  props: DiffListProps,
  field: { type: "assertion" | "score"; name: string },
  a: unknown,
  b: unknown,
): void {
  const { row } = props;
  if (isFlakyRow(row) && row.resultA && row.resultB) {
    props.onOpenDrawer({
      kind: "subrundiff",
      title: `${props.testLabel} · ${row.caseId} · ${field.name}`,
      caseKey: `${props.testLabel} · ${row.caseId}`,
      resultA: row.resultA,
      resultB: row.resultB,
      labelA: props.labelA,
      labelB: props.labelB,
      field,
    });
  } else {
    openOutcomePair(props, field.name, a, b);
  }
}

// Render each name with its BASE → COMPARE value. A click on a line shows the
// full result (reason + arguments) for both sides. An assertion carries no bar
// to annotate, only the value it reported.
function ValueDiffList(props: DiffListProps & { kind: "assertion" }) {
  const { row } = props;
  const [a, b] = [row.assertionsA, row.assertionsB];
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  if (keys.size === 0) return <span className="hint">—</span>;
  return (
    <ul className="evaluator-list compact">
      {[...keys].sort().map((k) => {
        const oa = a[k];
        const ob = b[k];
        const changed = oa?.value !== ob?.value;
        return (
          <li key={k} className={changed ? "evaluator-changed" : undefined}>
            <button
              className="evaluator-line-btn"
              type="button"
              title="open reason + arguments for A and B"
              onClick={() =>
                openEvaluator(props, { type: "assertion", name: k }, oa, ob)
              }
            >
              <code>
                {!changed ? (
                  `${k} ${boolSym(oa?.value)}`
                ) : (
                  <>
                    {k}: {boolSym(oa?.value)}{" "}
                    <span className="diff-arrow">→</span> {boolSym(ob?.value)}
                  </>
                )}
              </code>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

// Scores with optional per-case bars. Show BASE → COMPARE with the bar
// annotation when one exists. A click on a line shows both sides' full result.
// Each side is graded against the bar its own run recorded: a diff often shows
// a bar being tuned, and grading BASE against COMPARE's bar marks a run as
// failing a gate it never had.
function ScoreDiffList(props: DiffListProps) {
  const { row } = props;
  const keys = new Set([
    ...Object.keys(row.scoresA),
    ...Object.keys(row.scoresB),
  ]);
  if (keys.size === 0) return <span className="hint">—</span>;
  return (
    <ul className="evaluator-list compact">
      {[...keys].sort().map((k) => {
        const oa = row.scoresA[k];
        const ob = row.scoresB[k];
        // This row is a score row, so only its numeric value renders against
        // a bar. Anything else has no bar to clear.
        const va = oa ? (numericValue(oa) ?? undefined) : undefined;
        const vb = ob ? (numericValue(ob) ?? undefined) : undefined;
        const changed = va !== vb;
        const barA = oa?.bar ?? undefined;
        const barB = ob?.bar ?? undefined;
        return (
          <li key={k} className={changed ? "evaluator-changed" : undefined}>
            <button
              className="evaluator-line-btn"
              type="button"
              title="open reason + arguments for A and B"
              onClick={() =>
                openEvaluator(props, { type: "score", name: k }, oa, ob)
              }
            >
              <code>
                {!changed ? (
                  `${k}=${scoreText(va, barA)}`
                ) : (
                  <>
                    {k}: {scoreText(va, barA)}{" "}
                    <span className="diff-arrow">→</span> {scoreText(vb, barB)}
                  </>
                )}
              </code>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

type ResultValue = EvaluatorResult["value"] | undefined;

function boolSym(v: ResultValue): string {
  if (v === undefined) return "—";
  return v === true ? "✓" : "✗";
}

function scoreText(v: number | undefined, bar: number | undefined): string {
  if (v === undefined) return "—";
  const n = formatScore(v);
  if (bar === undefined) return n;
  return `${n}${scoreClearsBar(v, bar) ? "✓" : "✗"}`;
}
