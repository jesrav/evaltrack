import { useCallback, useEffect, useMemo, useState } from "react";
import { countAttemptsByOutcome } from "../attemptCounts";
import { summariseErrors } from "../attemptErrors";
import { formatDisplayText, getPrimaryView } from "../extract";
import { formatBytes } from "../format";
import { TextDiff } from "./TextDiff";
import { JsonView, diffCanCompare, isContainer } from "./JsonView";
import { JsonDiff } from "./JsonDiff";
import { OutcomeCard } from "./OutcomeCard";
import { ScoreMeter } from "./ScoreMeter";
import { ScoreTrendPanel } from "./ScoreTrend";
import {
  OUTCOME_LABEL,
  OUTCOME_MARK,
  findDecidingAttemptIndex,
  evaluatorPasses,
  getEvaluatorTone,
  recoveredOnARerun,
} from "../checks";
import {
  buildAnchorKey,
  buildCaseSections,
  focusAnchor,
  sectionOfKind,
} from "../caseSections";
import type { CaseFocus, CaseSectionId } from "../caseSections";
import type { CaseTrend } from "../scoreTrend";
import type {
  AttemptRecord,
  CaseRecord,
  EvaluatorResult,
  ResultKind,
} from "../types";
import { resultsOfKind } from "../results";

export type DrawerContent =
  /** A pane whose values are still loading. `size` is their total in bytes. */
  | {
      kind: "loading";
      title: string;
      size: number;
    }
  | {
      kind: "diff";
      title: string;
      subtitleA?: string;
      subtitleB?: string;
      a: unknown;
      b: unknown;
    }
  /** Same data shape as diff, but the two values are field-aware diffed via
   *  JsonDiff (no tabs, no line-by-line text diff). Right for small structured
   *  objects (two EvaluatorResults, say) where a line diff chops
   *  semantic fields into stripes. */
  | {
      kind: "pair";
      title: string;
      labelA: string;
      labelB: string;
      a: unknown;
      b: unknown;
    }
  /** Everything one case recorded, as one pane. The attempt selector comes
   *  first, then a section per recorded field. `caseKey` is the stable per-case key for the
   *  shared attempt selection. `focus` names the part of the case a click in
   *  the row asked for, so the pane opens scrolled to it. `trends` is this
   *  case's promoted score history keyed by score name, absent while it loads
   *  and when the fetch failed. */
  | {
      kind: "case";
      title: string;
      caseKey: string;
      caseResult: CaseRecord;
      trends?: Record<string, CaseTrend>;
      focus?: CaseFocus;
    }
  /** Attempt against attempt comparison. Two independent attempt selectors
   *  (BASE and COMPARE) and the field-aware diff of the two selected attempts.
   *  Defaults to each side's deciding attempt. */
  | {
      kind: "subrundiff";
      title: string;
      caseKey: string;
      resultA: CaseRecord;
      resultB: CaseRecord;
      labelA: string;
      labelB: string;
      field:
        | { type: "output" }
        | { type: "assertion"; name: string }
        | { type: "score"; name: string };
    };

/** Whether a value has a meaningful Raw view distinct from its Primary view.
 *  True only when `getPrimaryView` actually unwrapped it (it returns the same
 *  reference when it doesn't), so plain inputs/scalars don't get a noise toggle. */
function offersRaw(value: unknown): boolean {
  return getPrimaryView(value) !== value;
}

interface Props {
  content: DrawerContent | null;
  onClose: () => void;
  /** Shared per-case attempt selection (keyed by caseKey) so the field panes'
   *  strip and the run-view row stay in sync. */
  attemptSel: Record<string, number>;
  onSelectAttempt: (key: string, index: number) => void;
}

// A side-panel overlay. The "raw" tab is only useful when the primary-view
// heuristic actually unwrapped something (Pydantic AI's AgentRunResult is the
// canonical case). For plain inputs, structured EvaluationResults, and other
// values where primary == raw, the tab bar is hidden and the one meaningful
// view renders.
export function Drawer({
  content,
  onClose,
  attemptSel,
  onSelectAttempt,
}: Props) {
  const [tab, setTab] = useState<"primary" | "raw">("primary");

  // Reset to "primary" only when a new payload opens. Keyed on the content
  // identity rather than the selected attempt, which keeps the same content
  // object, so the chosen Primary/Raw mode persists across attempts.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- deliberate reset on prop change. The tab must survive re-renders (so no key remount) and only reset when a new payload opens.
    setTab("primary");
  }, [content]);

  useEffect(() => {
    if (!content) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [content, onClose]);

  const showRawTab = useMemo(() => {
    if (!content) return false;
    // The field-diff pair has no raw-vs-primary distinction, so it keeps a
    // single view.
    if (content.kind === "pair" || content.kind === "loading") return false;
    if (content.kind === "diff") {
      return offersRaw(content.a) || offersRaw(content.b);
    }
    if (content.kind === "subrundiff") {
      if (content.field.type !== "output") return false;
      const ia =
        attemptSel[`${content.caseKey}::base`] ??
        findDecidingAttemptIndex(content.resultA);
      const ib =
        attemptSel[`${content.caseKey}::compare`] ??
        findDecidingAttemptIndex(content.resultB);
      const attemptsA = content.resultA.attempts;
      const attemptsB = content.resultB.attempts;
      const oa = (attemptsA[ia] ?? attemptsA[0])?.output;
      const ob = (attemptsB[ib] ?? attemptsB[0])?.output;
      return offersRaw(oa) || offersRaw(ob);
    }
    // For a case, the produced output is the one value that can be a wrapped
    // result. An expected output, an input and metadata are recorded as written.
    const idx =
      attemptSel[content.caseKey] ??
      findDecidingAttemptIndex(content.caseResult);
    const attempts = content.caseResult.attempts;
    const out = (attempts[idx] ?? attempts[0])?.output;
    return offersRaw(out);
  }, [content, attemptSel]);

  if (!content) return null;
  const activeTab = showRawTab ? tab : "primary";
  const showTabBar = showRawTab;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer"
        onClick={(e) => e.stopPropagation()}
        aria-label="value inspector"
      >
        <header className="drawer-header">
          <div>
            <div className="drawer-title">{content.title}</div>
          </div>
          <button className="drawer-close" type="button" onClick={onClose}>
            ✕
          </button>
        </header>
        {showTabBar && (
          <div className="drawer-tabs">
            <button
              type="button"
              className={tab === "primary" ? "tab active" : "tab"}
              onClick={() => setTab("primary")}
            >
              {content.kind === "diff" ? "Diff" : "Primary"}
            </button>
            <button
              type="button"
              className={tab === "raw" ? "tab active" : "tab"}
              onClick={() => setTab("raw")}
            >
              Raw
            </button>
          </div>
        )}
        <div className="drawer-body">
          {content.kind === "loading" ? (
            <div className="empty-state run-loading">
              <span className="spinner" aria-hidden="true" />
              Loading {formatBytes(content.size)}…
            </div>
          ) : content.kind === "pair" ? (
            // Pair = field-aware diff of two structured values (e.g. two
            // EvaluatorResults). BASE/COMPARE labels live in the legend below.
            <PairPane
              a={content.a}
              b={content.b}
              labelA={content.labelA}
              labelB={content.labelB}
            />
          ) : content.kind === "case" ? (
            <CasePane
              caseResult={content.caseResult}
              trends={content.trends}
              focus={content.focus}
              tab={activeTab}
              selectedIndex={
                attemptSel[content.caseKey] ??
                findDecidingAttemptIndex(content.caseResult)
              }
              onSelect={(i) => onSelectAttempt(content.caseKey, i)}
            />
          ) : content.kind === "subrundiff" ? (
            <SubrunDiffPane
              resultA={content.resultA}
              resultB={content.resultB}
              labelA={content.labelA}
              labelB={content.labelB}
              field={content.field}
              tab={activeTab}
              selA={
                attemptSel[`${content.caseKey}::base`] ??
                findDecidingAttemptIndex(content.resultA)
              }
              selB={
                attemptSel[`${content.caseKey}::compare`] ??
                findDecidingAttemptIndex(content.resultB)
              }
              onSelectA={(i) => onSelectAttempt(`${content.caseKey}::base`, i)}
              onSelectB={(i) =>
                onSelectAttempt(`${content.caseKey}::compare`, i)
              }
            />
          ) : (
            <DiffPane
              a={content.a}
              b={content.b}
              subtitleA={content.subtitleA}
              subtitleB={content.subtitleB}
              tab={activeTab}
            />
          )}
        </div>
      </aside>
    </div>
  );
}

// The verdict rollup + tick strip for the case-field pane. The strip shows the
// whole pass/fail pattern, selects which attempt the pane shows, and offers a
// "first failing" jump. Selection is the shared per-case state. The strip wraps
// and scrolls so it scales to many attempts.
function AttemptSelector({
  caseResult,
  selectedIndex,
  onSelect,
}: {
  caseResult: CaseRecord;
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  const { passed_attempts, clean_attempts, outcome } = caseResult;
  const attempts = caseResult.attempts;
  const recovered = recoveredOnARerun(caseResult);
  const firstFailing = attempts.findIndex((a) => a.outcome !== "passed");
  const counts = countAttemptsByOutcome(caseResult);
  return (
    <>
      <div className="attempts-rollup">
        <span className={`pill ${OUTCOME_LABEL[outcome]}`}>
          {OUTCOME_LABEL[outcome]}
        </span>
        <span className="attempts-rollup-count">
          {counts ? (
            counts.map((c, i) => (
              <span key={c.label}>
                {i > 0 && " · "}
                <strong>{c.n}</strong> {c.label}
              </span>
            ))
          ) : (
            <>
              <strong>
                {passed_attempts}/{clean_attempts}
              </strong>{" "}
              attempts passed
            </>
          )}
        </span>
        {recovered && (
          <span
            className="pill warn"
            title="The first attempt failed and a later attempt passed."
          >
            ↻ recovered
          </span>
        )}
        {firstFailing >= 0 && firstFailing !== selectedIndex && (
          <button
            type="button"
            className="attempt-jump"
            title="Jump to the first failing attempt"
            onClick={() => onSelect(firstFailing)}
          >
            ↪ first failing
          </button>
        )}
      </div>
      <div className="attempt-strip" role="tablist" aria-label="attempts">
        {attempts.map((a, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === selectedIndex}
            className={`attempt-tick ${OUTCOME_LABEL[a.outcome]}${
              i === selectedIndex ? " active" : ""
            }`}
            title={`attempt ${i + 1}: ${OUTCOME_LABEL[a.outcome]}${
              summariseErrors(a.errors) ? `. ${summariseErrors(a.errors)}` : ""
            }`}
            onClick={() => onSelect(i)}
          >
            <span className="attempt-tick-idx">{i + 1}</span>
            <span className="attempt-tick-mark">{OUTCOME_MARK[a.outcome]}</span>
          </button>
        ))}
      </div>
    </>
  );
}

/** Everything that raised while producing or judging the shown attempt, each
 *  named by what raised it. The recorded line is the exception type and
 *  message; the full traceback stays in the test output. */
function AttemptErrors({ attempt }: { attempt: AttemptRecord | undefined }) {
  if (attempt?.outcome !== "errored") return null;
  const errors = attempt.errors ?? [];
  return (
    <div className="attempt-error" role="note">
      <span className="attempt-error-label">Errored</span>
      {errors.length > 0 ? (
        <ul className="attempt-error-list">
          {errors.map((e, i) => (
            <li key={i}>
              <span className="attempt-error-source">
                {e.evaluator ?? "the task"}
              </span>
              <code className="attempt-error-message">{e.message}</code>
            </li>
          ))}
        </ul>
      ) : (
        <span className="hint">
          The task or an evaluator raised. This run was recorded before the
          message was captured; see the pytest output.
        </span>
      )}
    </div>
  );
}

// Everything one case recorded, as one scrollable pane. The verdict and the
// attempt selector come first, then a section per recorded field. `buildCaseSections` decides
// which sections exist, so a value the case never recorded leaves no empty
// heading behind. Switching attempts re-derives the whole pane and updates the
// shared selection, so the row in the table follows.
function CasePane({
  caseResult,
  trends,
  focus,
  tab,
  selectedIndex,
  onSelect,
}: {
  caseResult: CaseRecord;
  trends: Record<string, CaseTrend> | undefined;
  focus: CaseFocus | undefined;
  tab: "primary" | "raw";
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  const attempts = caseResult.attempts;
  const current = attempts[selectedIndex] ?? attempts[0];
  const sections = buildCaseSections(caseResult, current);
  const anchor = focusAnchor(sections, current, focus);
  // A callback ref scrolls as soon as the wanted element exists, so a pane
  // opened from a cell never shows the top first and then jumps.
  const scrollToFocus = useCallback((el: HTMLElement | null) => {
    el?.scrollIntoView({ block: "start" });
  }, []);

  return (
    <div className="case-pane">
      <CaseVerdict
        caseResult={caseResult}
        selectedIndex={selectedIndex}
        onSelect={onSelect}
      />
      {sections.map((section) => (
        <section
          className="case-section"
          key={section.id}
          ref={
            anchor === buildAnchorKey(section.id) ? scrollToFocus : undefined
          }
        >
          <h3 className="case-section-title">{section.label}</h3>
          <CaseSectionBody
            section={section.id}
            caseResult={caseResult}
            attempt={current}
            trends={trends}
            anchor={anchor}
            scrollToFocus={scrollToFocus}
            tab={tab}
          />
        </section>
      ))}
    </div>
  );
}

// The case verdict at the top of the pane. A repeated case gets the attempt
// selector, which carries the verdict pill itself, so the pill stands alone
// only for a case that ran once.
function CaseVerdict({
  caseResult,
  selectedIndex,
  onSelect,
}: {
  caseResult: CaseRecord;
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  if (caseResult.attempts.length > 1) {
    return (
      <AttemptSelector
        caseResult={caseResult}
        selectedIndex={selectedIndex}
        onSelect={onSelect}
      />
    );
  }
  return (
    <div className="attempts-rollup">
      <span className={`pill ${OUTCOME_LABEL[caseResult.outcome]}`}>
        {OUTCOME_LABEL[caseResult.outcome]}
      </span>
    </div>
  );
}

function CaseSectionBody({
  section,
  caseResult,
  attempt,
  trends,
  anchor,
  scrollToFocus,
  tab,
}: {
  section: CaseSectionId;
  caseResult: CaseRecord;
  attempt: AttemptRecord | undefined;
  trends: Record<string, CaseTrend> | undefined;
  anchor: string | null;
  scrollToFocus: (el: HTMLElement | null) => void;
  tab: "primary" | "raw";
}) {
  switch (section) {
    case "input":
      return <JsonView value={caseResult.inputs} />;
    case "comparison":
      return (
        <ExpectedPane
          expected={caseResult.expected_output}
          output={attempt?.output}
          tab={tab}
        />
      );
    case "expected":
      return <JsonView value={caseResult.expected_output} />;
    case "output":
      return (
        <JsonView
          value={
            tab === "raw" ? attempt?.output : getPrimaryView(attempt?.output)
          }
        />
      );
    case "metadata":
      return <JsonView value={caseResult.metadata} />;
    case "scores":
      return (
        <CaseEvaluators
          kind="score"
          results={resultsOfKind(attempt?.results, "score")}
          trends={trends}
          anchor={anchor}
          scrollToFocus={scrollToFocus}
        />
      );
    case "assertions":
      return (
        <CaseEvaluators
          kind="assertion"
          results={resultsOfKind(attempt?.results, "assertion")}
          anchor={anchor}
          scrollToFocus={scrollToFocus}
        />
      );
    case "error":
      return <AttemptErrors attempt={attempt} />;
  }
}

// The results of one kind for the shown attempt, one card each. The name
// carries the verdict colour, so a long list still shows which one failed. A
// score also carries its trend, under the value the trend ends on.
function CaseEvaluators({
  kind,
  results,
  trends,
  anchor,
  scrollToFocus,
}: {
  kind: ResultKind;
  results: Record<string, EvaluatorResult>;
  trends?: Record<string, CaseTrend>;
  anchor: string | null;
  scrollToFocus: (el: HTMLElement | null) => void;
}) {
  const section = sectionOfKind(kind);
  return (
    <div className="case-evaluators">
      {Object.entries(results).map(([name, result]) => {
        const bar = kind === "score" ? (result.bar ?? undefined) : undefined;
        const passes = evaluatorPasses(result);
        return (
          <div
            className="case-evaluator"
            key={name}
            ref={
              anchor === buildAnchorKey(section, name)
                ? scrollToFocus
                : undefined
            }
          >
            <div className="case-evaluator-head">
              <span className={`pill ${getEvaluatorTone(result)}`}>{name}</span>
              {typeof result.value === "number" && (
                <ScoreMeter value={result.value} bar={bar} passes={passes} />
              )}
            </div>
            <OutcomeCard outcome={result} />
            {kind === "score" && trends && (
              <ScoreTrendPanel trend={trends[name]} />
            )}
          </div>
        );
      })}
    </div>
  );
}

const FIELD_TITLE: Record<ResultKind, string> = {
  assertion: "Assertion",
  score: "Score",
};

// Attempt against attempt comparison. A BASE selector and a COMPARE selector,
// independent because the attempt sets are not paired, then the diff of one
// field between the two attempts. Output diffs via the shared DiffPane
// (Primary/Raw). A named assertion or score diffs both sides' results via
// PairPane.
function SubrunDiffPane({
  resultA,
  resultB,
  labelA,
  labelB,
  field,
  tab,
  selA,
  selB,
  onSelectA,
  onSelectB,
}: {
  resultA: CaseRecord;
  resultB: CaseRecord;
  labelA: string;
  labelB: string;
  field:
    | { type: "output" }
    | { type: "assertion"; name: string }
    | { type: "score"; name: string };
  tab: "primary" | "raw";
  selA: number;
  selB: number;
  onSelectA: (i: number) => void;
  onSelectB: (i: number) => void;
}) {
  const attemptsA = resultA.attempts;
  const attemptsB = resultB.attempts;
  const aAtt = attemptsA[selA] ?? attemptsA[0];
  const bAtt = attemptsB[selB] ?? attemptsB[0];
  const outcomeOf = (att: typeof aAtt) =>
    att && field.type !== "output" ? att.results[field.name] : undefined;

  return (
    <div className="attempts-pane">
      <div className="subrun-side">
        <div className="subrun-side-label base">BASE · {labelA}</div>
        <AttemptSelector
          caseResult={resultA}
          selectedIndex={selA}
          onSelect={onSelectA}
        />
      </div>
      <div className="subrun-side">
        <div className="subrun-side-label compare">COMPARE · {labelB}</div>
        <AttemptSelector
          caseResult={resultB}
          selectedIndex={selB}
          onSelect={onSelectB}
        />
      </div>
      {field.type === "output" ? (
        <div className="attempt-section">
          <span className="attempt-section-label">
            Output{tab === "raw" ? " (raw)" : ""}
          </span>
          <DiffPane
            a={aAtt?.output}
            b={bAtt?.output}
            subtitleA="BASE"
            subtitleB="COMPARE"
            tab={tab}
          />
        </div>
      ) : (
        <div className="attempt-section">
          <span className="attempt-section-label">
            {FIELD_TITLE[field.type]} · {field.name}
          </span>
          <PairPane
            a={outcomeOf(aAtt) ?? null}
            b={outcomeOf(bAtt) ?? null}
            labelA={labelA}
            labelB={labelB}
          />
        </div>
      )}
    </div>
  );
}

function PairPane({
  a,
  b,
  labelA,
  labelB,
}: {
  a: unknown;
  b: unknown;
  labelA: string;
  labelB: string;
}) {
  return (
    <div className="json-diff-pane">
      <div className="text-diff-legend">
        <span className="legend-pill removed">− BASE · {labelA}</span>
        <span className="legend-pill added">+ COMPARE · {labelB}</span>
      </div>
      <JsonDiff a={a} b={b} />
    </div>
  );
}

// A case's target beside its result. The diff leads, because on a failed
// comparison the difference is the whole answer. Both plain values follow,
// because a diff of two large objects dims everything that matched, which is
// most of it.
function ExpectedPane({
  expected,
  output,
  tab,
}: {
  expected: unknown;
  output: unknown;
  tab: "primary" | "raw";
}) {
  return (
    <div className="attempts-pane">
      {diffCanCompare(expected, output) && (
        <div className="attempt-section">
          <span className="attempt-section-label">Difference</span>
          <DiffPane
            a={expected}
            b={output}
            labelA="EXPECTED"
            labelB="OUTPUT"
            tab={tab}
          />
        </div>
      )}
      <div className="attempt-section">
        <span className="attempt-section-label">Expected</span>
        <JsonView value={expected} />
      </div>
      <div className="attempt-section">
        <span className="attempt-section-label">
          Output{tab === "raw" ? " (raw)" : ""}
        </span>
        <JsonView value={tab === "raw" ? output : getPrimaryView(output)} />
      </div>
    </div>
  );
}

function DiffPane({
  a,
  b,
  subtitleA,
  subtitleB,
  labelA = "BASE",
  labelB = "COMPARE",
  tab,
}: {
  a: unknown;
  b: unknown;
  subtitleA?: string;
  subtitleB?: string;
  /** Names for the two sides. Default to the run-comparison pair. */
  labelA?: string;
  labelB?: string;
  tab: "primary" | "raw";
}) {
  // A is always the BASE side, B the COMPARE side. Name and colour them so the
  // two columns match the run pickers rather than only the run id.
  if (tab === "primary") {
    // Field-aware diff when both unwrapped values are objects/arrays. Fall back
    // to the line-by-line TextDiff for plain strings (and primitives), where a
    // per-key diff has nothing to key on.
    const pa = getPrimaryView(a);
    const pb = getPrimaryView(b);
    if (isContainer(pa) && isContainer(pb)) {
      return (
        <div className="json-diff-pane">
          <div className="text-diff-legend">
            <span className="legend-pill removed">
              − {labelA}
              {subtitleA ? ` · ${subtitleA}` : ""}
            </span>
            <span className="legend-pill added">
              + {labelB}
              {subtitleB ? ` · ${subtitleB}` : ""}
            </span>
          </div>
          <JsonDiff a={pa} b={pb} labelA={labelA} labelB={labelB} />
        </div>
      );
    }
    return (
      <TextDiff
        a={formatDisplayText(a, true)}
        b={formatDisplayText(b, true)}
        labelA={subtitleA ? `${labelA} · ${subtitleA}` : labelA}
        labelB={subtitleB ? `${labelB} · ${subtitleB}` : labelB}
      />
    );
  }
  // The raw tab is a field-aware diff of the original, un-unwrapped values, via
  // the same JSON renderer as the primary tab.
  return (
    <div className="json-diff-pane">
      <div className="text-diff-legend">
        <span className="legend-pill removed">
          − {labelA}
          {subtitleA ? ` · ${subtitleA}` : ""}
        </span>
        <span className="legend-pill added">
          + {labelB}
          {subtitleB ? ` · ${subtitleB}` : ""}
        </span>
      </div>
      <JsonDiff a={a} b={b} labelA={labelA} labelB={labelB} />
    </div>
  );
}
