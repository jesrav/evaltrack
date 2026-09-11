// Renders the per-attempt evaluator output as a stack of pills. Each pill is
// clickable and opens the case inspector scrolled to that evaluator, where the
// value, the reason, and `evaluator.arguments` (e.g. the params a ToolCalled
// evaluator was checking) are shown in full.
// The reason shows inline below failing assertions and any score that has one,
// so the most useful explanation is visible without a click.

import { evaluatorPasses, getEvaluatorTone } from "../checks";
import { truncate } from "../extract";
import { formatScore } from "../format";
import { hasPromotedHistory } from "../scoreTrend";
import { ScoreMeter } from "./ScoreMeter";
import { sectionOfKind } from "../caseSections";
import type { CaseFocus } from "../caseSections";
import type { CaseTrend } from "../scoreTrend";
import type { EvaluatorResult, ResultKind } from "../types";
import { numericValue } from "../results";

interface AssertionProps {
  kind: "assertion";
  results: Record<string, EvaluatorResult>;
  onOpenCase: (focus: CaseFocus) => void;
}

interface ScoreProps {
  kind: "score";
  results: Record<string, EvaluatorResult>;
  // This case's promoted score history, keyed by score name. A score that has
  // one gets a link into its panel. The rest have nothing to show.
  trends?: Record<string, CaseTrend>;
  onOpenCase: (focus: CaseFocus) => void;
}

type Props = AssertionProps | ScoreProps;

export function EvaluatorList(props: Props) {
  // Widen the union-of-records to one record type so Object.entries keeps a
  // precise value type instead of falling back to `any`.
  const results: Record<string, EvaluatorResult> = props.results;
  const entries = Object.entries(results);
  if (entries.length === 0) return <span className="hint">—</span>;
  const section = sectionOfKind(props.kind);
  const trends = props.kind === "score" ? props.trends : undefined;
  return (
    <ul className="evaluator-list">
      {entries.map(([name, result]) => (
        <li key={name}>
          <EvaluatorRow
            name={name}
            result={result}
            kind={props.kind}
            bar={props.kind === "score" ? (result.bar ?? undefined) : undefined}
            onOpen={() => props.onOpenCase({ section, name })}
          />
          {/* Sibling of the pill rather than inside it, because the pill is a
              button and one button cannot hold another. Both open the same pane,
              so the link advertises the trend rather than adding a second way
              in. */}
          {trends && hasPromotedHistory(trends[name]?.points ?? []) && (
            <button
              type="button"
              className="trend-link"
              aria-label={`see the ${name} trend`}
              title="This score across the promoted runs."
              onClick={() => props.onOpenCase({ section, name })}
            >
              <svg
                className="trend-link-glyph"
                viewBox="0 0 16 10"
                aria-hidden="true"
              >
                <path d="M1 8 L5.5 3.5 L9 6 L15 1.5" />
              </svg>
              trend
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function EvaluatorRow({
  name,
  result,
  kind,
  bar,
  onOpen,
}: {
  name: string;
  result: EvaluatorResult;
  kind: ResultKind;
  bar: number | undefined;
  onOpen: () => void;
}) {
  const passes = evaluatorPasses(result);

  // Show the reason inline for failing assertions (most useful) and for any
  // score that carries one. Passing assertions hide it to keep rows tight, since
  // it's still one click away in the drawer.
  const reason = result.reason ?? "";
  const showReasonInline =
    reason !== "" && (kind === "score" || result.value !== true);

  // Scores render as a rounded meter (with a bar tick) below the name
  // pill. Assertions and any non-numeric score fall back to the inline text.
  const scoreAsMeter = kind === "score" && typeof result.value === "number";

  return (
    <button
      className="evaluator-row"
      type="button"
      onClick={onOpen}
      title="open this case, at this evaluator"
    >
      <span className={`pill ${getEvaluatorTone(result)}`}>
        {name}
        {kind === "assertion" ? (
          <span className="pill-sym">{result.value === true ? "✓" : "✗"}</span>
        ) : (
          !scoreAsMeter && (
            <span className="pill-val">
              ={formatValue(result.value)}
              {bar !== undefined &&
                ` (≥ ${formatValue(bar)} ${passes ? "✓" : "✗"})`}
            </span>
          )
        )}
      </span>
      {scoreAsMeter && (
        <ScoreMeter
          value={numericValue(result) ?? 0}
          bar={bar}
          passes={passes}
        />
      )}
      {showReasonInline && (
        // The title is capped because a verbose judge can write thousands of
        // characters, and a native tooltip that long covers the viewport with
        // no way to scroll or dismiss it. The full text is in the drawer.
        <span className="evaluator-reason" title={truncate(reason, 300)}>
          {truncate(reason, 90)}
        </span>
      )}
    </button>
  );
}

function formatValue(v: EvaluatorResult["value"]): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return formatScore(v);
  return String(v);
}
