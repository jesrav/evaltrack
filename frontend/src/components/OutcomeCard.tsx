// Purpose-built card for a single EvaluatorResult in the drawer. Shows the
// value, the reason and the evaluator as prose rather than raw JSON, and keeps
// every other key under "other", so nothing recorded is dropped.
//
// The verdict mark (✓/✗) is the one place green/red is allowed, because it is a
// pass/fail signal. Everything else follows JsonView's colour language.

import { formatFallbackString } from "../extract";
import { formatScore } from "../format";
import { JsonView } from "./JsonView";
import type { EvaluatorResult } from "../types";

// Keys with dedicated rendering, plus the fields that every serialised result
// carries and the card does not show. If listed as extras, they put a tree of
// nulls under every card.
const TYPED_KEYS = new Set([
  "value",
  "reason",
  "evaluator",
  "details",
  "verdict",
  "bar",
  "runner_bar",
  "marker_bar",
]);

/** The keys of a result that the card has no dedicated rendering for. */
export function otherKeys(blob: Record<string, unknown>): string[] {
  return Object.keys(blob).filter((k) => !TYPED_KEYS.has(k));
}

export function OutcomeCard({ outcome }: { outcome: EvaluatorResult }) {
  // Treat the outcome as an opaque record so its real keys are iterated
  // rather than assuming exactly {value, reason, evaluator}.
  const blob = (outcome ?? {}) as unknown as Record<string, unknown>;
  const value = blob.value;
  const reason = blob.reason as string | null | undefined;
  const evaluator = blob.evaluator as EvaluatorResult["evaluator"];
  const details = blob.details as EvaluatorResult["details"];
  const extras = otherKeys(blob);

  return (
    <div className="outcome-card">
      <div className="outcome-value-row">
        <span className="outcome-value-label">value</span>
        <OutcomeValue value={value} />
      </div>

      <div className="outcome-section">
        <div className="outcome-section-label">reason</div>
        {reason ? (
          <p className="outcome-reason">{reason}</p>
        ) : (
          <span className="json-null">(none)</span>
        )}
      </div>

      <div className="outcome-section">
        <div className="outcome-section-label">evaluator</div>
        {evaluator ? (
          <div className="outcome-source">
            <div className="outcome-source-name">
              <span className="json-key">name</span>
              <span className="json-colon">:</span>{" "}
              <span className="json-string">{evaluator.name}</span>
            </div>
            {/* An evaluator built with no arguments is the common case, so it
                sits on the name's line instead of opening a block. */}
            {evaluator.arguments == null ? (
              <div className="outcome-source-name">
                <span className="json-key">arguments</span>
                <span className="json-colon">:</span>{" "}
                <span className="json-null">—</span>
              </div>
            ) : (
              <div className="outcome-source-args">
                <span className="json-key">arguments</span>
                <span className="json-colon">:</span>{" "}
                <JsonView value={evaluator.arguments} />
              </div>
            )}
          </div>
        ) : (
          <span className="json-null">—</span>
        )}
      </div>

      {/* What the evaluator answered, as against how it was configured. Given
          its own section rather than left to "other", because the judge's own
          working is what a reader opens the case for. */}
      <div className="outcome-section">
        <div className="outcome-section-label">details</div>
        {details && Object.keys(details).length > 0 ? (
          <JsonView value={details} />
        ) : (
          <span className="json-null">—</span>
        )}
      </div>

      {extras.length > 0 && (
        <div className="outcome-section">
          <div className="outcome-section-label">other</div>
          <JsonView
            value={Object.fromEntries(extras.map((k) => [k, blob[k]]))}
          />
        </div>
      )}
    </div>
  );
}

// The prominent value: ✓/✗ for booleans (with the pass/fail palette, since this is a
// verdict), the number for scores, the raw text otherwise.
function OutcomeValue({ value }: { value: unknown }) {
  if (typeof value === "boolean") {
    return (
      <span className={`outcome-verdict ${value ? "pass" : "fail"}`}>
        {value ? "✓ true" : "✗ false"}
      </span>
    );
  }
  if (typeof value === "number") {
    return <span className="outcome-score">{formatScore(value)}</span>;
  }
  if (value === null || value === undefined) {
    return <span className="json-null">—</span>;
  }
  return <span className="outcome-score">{formatFallbackString(value)}</span>;
}
