// Pure functions that turn two RunRecords into a comparison shape the UI
// renders.
//
// The diff lives at the case level. For each shared (test, case) it compares
// the per-case `outcome` and the per-attempt evaluator results. A test
// "regresses" when any case flips pass -> fail, and "recovers" when one flips
// fail -> pass. An errored case reached no verdict, so a pair with an errored
// side is counted apart (`casesErrored`), never as a flip or a hold. A test in
// both runs gets a case-level diff whether or not it evaluated on each side. A
// test that crashed or was skipped before evaluating recorded no cases, so its
// diff carries only the outcome flip. A test in one run alone is listed
// one-sided.
//
// Cases that didn't flip are split into `stillPassing` and `stillFailing`
// rather than one "unchanged" bucket, and a dropped score is counted even when
// the verdict held. Both are ways a run can be worse while every flip counter
// is zero.

import { formatCanonicalText } from "./extract";
import { findDecidingAttempt } from "./checks";
import type {
  CaseRecord,
  RunRecord,
  EvaluatorResult,
  TestOutcome,
} from "./types";
import { numericValue, resultsOfKind } from "./results";

export type CaseStatus = "passing" | "failing" | "errored" | "missing";

/** How a test's overall verdict moved between the two runs, derived from its
 *  case-level flips. */
export type VerdictChange = "regressed" | "recovered" | "unchanged";

export interface CaseRow {
  caseId: string;
  a: CaseStatus;
  b: CaseStatus;
  // Assertions, scores and output are taken from each side's DECIDING
  // attempt (the representative the single-run view shows). Full outcomes
  // (value + reason + source) so the diff can open the detail.
  assertionsA: Record<string, EvaluatorResult>;
  assertionsB: Record<string, EvaluatorResult>;
  scoresA: Record<string, EvaluatorResult>;
  scoresB: Record<string, EvaluatorResult>;
  inputA: unknown;
  inputB: unknown;
  outputA: unknown;
  outputB: unknown;
  inputChanged: boolean;
  outputChanged: boolean;
  scoresChanged: boolean;
  // Latency (seconds) of each side's deciding attempt, and whether they differ
  // by a meaningful margin (see `latencyChanged`). Informational, not folded
  // into `changed`, since slower isn't a regression.
  durationA: number | null;
  durationB: number | null;
  durationChanged: boolean;
  changed: boolean;
  // Per-side pass-rate (the flakiness signal in a diff) and the full case
  // results, so the attempt comparison drawer can pick any attempt per side.
  passesA: number;
  cleanAttemptsA: number;
  passesB: number;
  cleanAttemptsB: number;
  resultA: CaseRecord | null;
  resultB: CaseRecord | null;
}

export interface TestDiff {
  nodeid: string;
  cases: CaseRow[];
  /** Whether the test regressed, recovered, or held between the two runs,
   *  based on its case-verdict flips. */
  verdictChange: VerdictChange;
  /** The test's pytest outcome on each side. This can change even when every
   *  case verdict is identical. Adding an `xfail` marker turns the same failing
   *  case into an expected failure. Surfacing it keeps such a flip from showing
   *  as "no changes". */
  outcomeA: TestOutcome | null;
  outcomeB: TestOutcome | null;
  outcomeChanged: boolean;
  newlyFailing: number;
  newlyPassing: number;
  /** Shared cases where either side errored. No verdict to compare, so these
   *  never feed the flip or hold counts. */
  errored: number;
  stillPassing: number;
  stillFailing: number;
  scoresRegressed: number;
  scoresImproved: number;
  onlyInA: number;
  onlyInB: number;
}

export interface RunDiff {
  tests: TestDiff[];
  testsOnlyInA: string[];
  testsOnlyInB: string[];
  totals: {
    /** Tests with at least one case that flipped pass → fail and none that
     *  flipped the other way. This is the headline regression signal. */
    testsRegressed: number;
    testsRecovered: number;
    /** Number of tests whose pytest outcome changed between runs. */
    outcomeFlips: number;
    newlyFailing: number;
    newlyPassing: number;
    /** Shared cases where either side errored (reached no verdict). */
    casesErrored: number;
    stillPassing: number;
    stillFailing: number;
    scoresRegressed: number;
    scoresImproved: number;
    casesOnlyInA: number;
    casesOnlyInB: number;
  };
}

function getCaseStatus(c: CaseRecord): CaseStatus {
  if (c.outcome === "passed") return "passing";
  if (c.outcome === "errored") return "errored";
  return "failing";
}

function getScores(c: CaseRecord): Record<string, EvaluatorResult> {
  return resultsOfKind(findDecidingAttempt(c)?.results, "score");
}

function getAssertions(c: CaseRecord): Record<string, EvaluatorResult> {
  return resultsOfKind(findDecidingAttempt(c)?.results, "assertion");
}

function getOutput(c: CaseRecord): unknown {
  return findDecidingAttempt(c)?.output;
}

function getDuration(c: CaseRecord): number | null {
  return findDecidingAttempt(c)?.task_duration ?? null;
}

// Latency is jittery, so a diff only flags a change above both a relative and
// an absolute threshold, which avoids "+0.02s" noise on every case.
const LATENCY_REL_THRESHOLD = 0.2; // 20%
const LATENCY_ABS_THRESHOLD = 0.1; // seconds

/** Whether two deciding-attempt latencies differ enough to surface. Returns
 *  false when either side is unrecorded (null). */
export function latencyChanged(a: number | null, b: number | null): boolean {
  if (a == null || b == null) return false;
  const abs = Math.abs(b - a);
  const rel = a === 0 ? (b === 0 ? 0 : Infinity) : abs / a;
  return abs >= LATENCY_ABS_THRESHOLD && rel >= LATENCY_REL_THRESHOLD;
}

/** Classify a test's overall transition from its per-case counts. Any case
 *  going pass → fail is a regression. Otherwise any case going fail → pass is
 *  a recovery. Otherwise unchanged. Regression takes precedence, so a mixed
 *  test shows as the worse of the two. Errored cases feed neither count.
 *  A crash surfaces through `outcomeChanged` and the errored counter, not as
 *  a verdict move. */
function classifyVerdict(
  newlyFailing: number,
  newlyPassing: number,
): VerdictChange {
  if (newlyFailing > 0) return "regressed";
  if (newlyPassing > 0) return "recovered";
  return "unchanged";
}

export function diffRuns(a: RunRecord, b: RunRecord): RunDiff {
  // Pairing is by presence, not by a recorded eval. A test that passed in one
  // run and crashed before evaluating in the other is the change a reader most
  // needs to see, and it only registers as an outcome flip when paired.
  const shared = Object.keys(a.tests)
    .filter((n) => n in b.tests)
    .sort();
  const onlyInA = Object.keys(a.tests)
    .filter((n) => !(n in b.tests))
    .sort();
  const onlyInB = Object.keys(b.tests)
    .filter((n) => !(n in a.tests))
    .sort();

  const tests: TestDiff[] = [];
  let totalTestsRegressed = 0;
  let totalTestsRecovered = 0;
  let totalOutcomeFlips = 0;
  let totalNewlyFailing = 0;
  let totalNewlyPassing = 0;
  let totalCasesErrored = 0;
  let totalStillPassing = 0;
  let totalStillFailing = 0;
  let totalScoresRegressed = 0;
  let totalScoresImproved = 0;
  let totalCasesOnlyInA = 0;
  let totalCasesOnlyInB = 0;

  for (const nodeid of shared) {
    const aCases = a.tests[nodeid]!.cases;
    const bCases = b.tests[nodeid]!.cases;
    const caseIds = new Set([...Object.keys(aCases), ...Object.keys(bCases)]);

    const rows: CaseRow[] = [];
    let newlyFailing = 0;
    let newlyPassing = 0;
    let errored = 0;
    let stillPassing = 0;
    let stillFailing = 0;
    let scoresRegressed = 0;
    let scoresImproved = 0;
    let oa = 0;
    let ob = 0;

    for (const name of [...caseIds].sort()) {
      const aCase = aCases[name];
      const bCase = bCases[name];
      const sa: CaseStatus = aCase ? getCaseStatus(aCase) : "missing";
      const sb: CaseStatus = bCase ? getCaseStatus(bCase) : "missing";
      const statusChanged = sa !== sb;

      if (sa === "missing") ob += 1;
      else if (sb === "missing") oa += 1;
      // An errored side reached no verdict, so the pair is neither a flip
      // nor a hold, and never a "newly failing".
      else if (sa === "errored" || sb === "errored") errored += 1;
      else if (sa === "passing" && sb === "failing") newlyFailing += 1;
      else if (sa === "failing" && sb === "passing") newlyPassing += 1;
      else if (sa === "passing") stillPassing += 1;
      else stillFailing += 1;

      const inputA = aCase?.inputs;
      const inputB = bCase?.inputs;
      const outputA = aCase ? getOutput(aCase) : undefined;
      const outputB = bCase ? getOutput(bCase) : undefined;
      const scoresA = aCase ? getScores(aCase) : {};
      const scoresB = bCase ? getScores(bCase) : {};
      const assertionsA = aCase ? getAssertions(aCase) : {};
      const assertionsB = bCase ? getAssertions(bCase) : {};

      // A crashed attempt is stamped task_duration 0.0, which looks like a
      // false latency win. An errored side reports no latency.
      const durationA = aCase && sa !== "errored" ? getDuration(aCase) : null;
      const durationB = bCase && sb !== "errored" ? getDuration(bCase) : null;

      const bothPresent = aCase != null && bCase != null;
      // Scores are compared only when both sides reached a verdict. A crashed
      // attempt's scores are whatever it recorded before the crash.
      const comparable = bothPresent && sa !== "errored" && sb !== "errored";
      const inputChanged =
        bothPresent &&
        formatCanonicalText(inputA) !== formatCanonicalText(inputB);
      const outputChanged =
        bothPresent &&
        formatCanonicalText(outputA) !== formatCanonicalText(outputB);
      // An errored side reached no verdict, so its scores are not a change.
      const scoresChanged = comparable && !valuesEqual(scoresA, scoresB);
      // A score move on a flipped case is already counted as the flip. Only
      // a held verdict needs the score to say the case moved.
      const verdictHeld = comparable && !statusChanged;
      if (verdictHeld && anyScoreMoved(scoresA, scoresB, -1))
        scoresRegressed += 1;
      if (verdictHeld && anyScoreMoved(scoresA, scoresB, 1))
        scoresImproved += 1;
      const durationChanged =
        bothPresent && latencyChanged(durationA, durationB);

      rows.push({
        caseId: name,
        a: sa,
        b: sb,
        assertionsA,
        assertionsB,
        scoresA,
        scoresB,
        inputA,
        inputB,
        outputA,
        outputB,
        inputChanged,
        outputChanged,
        scoresChanged,
        durationA,
        durationB,
        durationChanged,
        changed:
          statusChanged || inputChanged || outputChanged || scoresChanged,
        passesA: aCase?.passed_attempts ?? 0,
        cleanAttemptsA: aCase?.clean_attempts ?? 0,
        passesB: bCase?.passed_attempts ?? 0,
        cleanAttemptsB: bCase?.clean_attempts ?? 0,
        resultA: aCase ?? null,
        resultB: bCase ?? null,
      });
    }

    const verdictChange = classifyVerdict(newlyFailing, newlyPassing);
    if (verdictChange === "regressed") totalTestsRegressed += 1;
    else if (verdictChange === "recovered") totalTestsRecovered += 1;

    const outcomeA = a.tests[nodeid]?.outcome ?? null;
    const outcomeB = b.tests[nodeid]?.outcome ?? null;
    const outcomeChanged = outcomeA !== outcomeB;
    if (outcomeChanged) totalOutcomeFlips += 1;

    tests.push({
      nodeid,
      cases: rows,
      verdictChange,
      outcomeA,
      outcomeB,
      outcomeChanged,
      newlyFailing,
      newlyPassing,
      errored,
      stillPassing,
      stillFailing,
      scoresRegressed,
      scoresImproved,
      onlyInA: oa,
      onlyInB: ob,
    });
    totalNewlyFailing += newlyFailing;
    totalNewlyPassing += newlyPassing;
    totalCasesErrored += errored;
    totalStillPassing += stillPassing;
    totalStillFailing += stillFailing;
    totalScoresRegressed += scoresRegressed;
    totalScoresImproved += scoresImproved;
    totalCasesOnlyInA += oa;
    totalCasesOnlyInB += ob;
  }

  return {
    tests,
    testsOnlyInA: onlyInA,
    testsOnlyInB: onlyInB,
    totals: {
      testsRegressed: totalTestsRegressed,
      testsRecovered: totalTestsRecovered,
      outcomeFlips: totalOutcomeFlips,
      newlyFailing: totalNewlyFailing,
      newlyPassing: totalNewlyPassing,
      casesErrored: totalCasesErrored,
      stillPassing: totalStillPassing,
      stillFailing: totalStillFailing,
      scoresRegressed: totalScoresRegressed,
      scoresImproved: totalScoresImproved,
      casesOnlyInA: totalCasesOnlyInA,
      casesOnlyInB: totalCasesOnlyInB,
    },
  };
}

// Scores are jittery and have no universal scale, so a move counts only above
// both a relative and an absolute threshold, the same guard latency uses.
// Without it a one-run-vs-one-run comparison flags noise on nearly every case.
const SCORE_REL_THRESHOLD = 0.05; // 5%
const SCORE_ABS_THRESHOLD = 0.05;

/** Which way a score moved between two values, or 0 when the move is under
 *  either threshold. The one rule for whether a score moved, so a fall is the
 *  same wherever the dashboard reports one. */
function scoreMoved(before: number | null, after: number | null): -1 | 0 | 1 {
  // Only a number moves. An assertion or a word changing is a different fact,
  // and the pass/fail columns already carry it.
  if (before === null || after === null) return 0;
  const abs = Math.abs(after - before);
  const rel =
    before === 0 ? (after === 0 ? 0 : Infinity) : abs / Math.abs(before);
  if (abs < SCORE_ABS_THRESHOLD || rel < SCORE_REL_THRESHOLD) return 0;
  return after < before ? -1 : 1;
}

/** Whether any score the two sides share moved meaningfully in `direction`.
 *
 *  Counted separately from the verdict flips. A score can drop a long way and
 *  still clear its bar, or drop on a case that was already failing. A rollup
 *  built only from pass/fail flips then reports the run as clean. */
function anyScoreMoved(
  a: Record<string, EvaluatorResult>,
  b: Record<string, EvaluatorResult>,
  direction: -1 | 1,
): boolean {
  for (const [name, before] of Object.entries(a)) {
    const after = b[name];
    if (
      after !== undefined &&
      scoreMoved(numericValue(before), numericValue(after)) === direction
    )
      return true;
  }
  return false;
}

/** Whether the two sides recorded the same value under every name. A name on
 *  one side only counts as a difference. */
function valuesEqual(
  a: Record<string, EvaluatorResult>,
  b: Record<string, EvaluatorResult>,
): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (a[k]?.value !== b[k]?.value) return false;
  }
  return true;
}

/** Rollup of what a diff changed, for one test or for every test in a module.
 *  Every counter but `stillPassing` is something the reader has to look at. */
export interface DiffSummary {
  regressed: number;
  recovered: number;
  outcomeFlips: number;
  newlyFailing: number;
  newlyPassing: number;
  errored: number;
  /** Cases whose input, output or scores moved. A rewritten answer the
   *  assertions still accept moves no verdict counter, so without this one the
   *  rollup calls it no change. */
  contentChanged: number;
  stillPassing: number;
  stillFailing: number;
  scoresRegressed: number;
  scoresImproved: number;
  onlyInEither: number;
}

export function summariseTestDiff(t: TestDiff): DiffSummary {
  return {
    regressed: t.verdictChange === "regressed" ? 1 : 0,
    recovered: t.verdictChange === "recovered" ? 1 : 0,
    outcomeFlips: t.outcomeChanged ? 1 : 0,
    newlyFailing: t.newlyFailing,
    newlyPassing: t.newlyPassing,
    errored: t.errored,
    contentChanged: t.cases.filter(
      (c) => c.inputChanged || c.outputChanged || c.scoresChanged,
    ).length,
    stillPassing: t.stillPassing,
    stillFailing: t.stillFailing,
    scoresRegressed: t.scoresRegressed,
    scoresImproved: t.scoresImproved,
    onlyInEither: t.onlyInA + t.onlyInB,
  };
}

export function summariseDiffModule(
  nodeids: string[],
  testByNodeid: Map<string, TestDiff>,
): DiffSummary {
  const total: DiffSummary = {
    regressed: 0,
    recovered: 0,
    outcomeFlips: 0,
    newlyFailing: 0,
    newlyPassing: 0,
    errored: 0,
    contentChanged: 0,
    stillPassing: 0,
    stillFailing: 0,
    scoresRegressed: 0,
    scoresImproved: 0,
    onlyInEither: 0,
  };
  for (const name of nodeids) {
    const t = testByNodeid.get(name);
    if (!t) continue;
    const one = summariseTestDiff(t);
    for (const key of Object.keys(total) as (keyof DiffSummary)[]) {
      total[key] += one[key];
    }
  }
  return total;
}

/** Whether the block has something to open and read, which is also what its
 *  badge reports. Deciding both from one place keeps a card from showing a
 *  change while collapsed. A case that fails on both sides is not a change. On
 *  a PR-vs-mainline diff it is still what the reviewer has to see, so it
 *  counts, as does a case whose content moved under a held verdict. Only
 *  "still passing" does not. */
export function hasDiffChanges(summary: DiffSummary): boolean {
  return (
    summary.regressed > 0 ||
    summary.recovered > 0 ||
    summary.outcomeFlips > 0 ||
    summary.newlyFailing > 0 ||
    summary.newlyPassing > 0 ||
    summary.errored > 0 ||
    summary.contentChanged > 0 ||
    summary.stillFailing > 0 ||
    summary.scoresRegressed > 0 ||
    summary.scoresImproved > 0 ||
    summary.onlyInEither > 0
  );
}
