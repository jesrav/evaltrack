// Presentational only. Renders a numeric score as a rounded progress meter
// instead of bare text. The fill is colored by verdict (pass/fail), or neutral
// when the score has no bar, and a small tick marks the `bar` when one exists.
// The exact value and the "≥ bar" annotation stay visible as text alongside
// the meter, so nothing here is a hover-only affordance.
//
// Scores are assumed to live on a 0..1 scale (the pydantic-evals convention).
// Values are clamped into range. If a value or bar exceeds 1, the scale grows to
// the larger of the two so the meter stays meaningful rather than sitting at
// full.

import { formatScore } from "../format";

function clampPct(v: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(1, v / max)) * 100;
}

export function ScoreMeter({
  value,
  bar,
  passes,
}: {
  value: number;
  bar: number | undefined;
  passes: boolean;
}) {
  const max = Math.max(1, value, bar ?? 0);
  const fillPct = clampPct(value, max);
  const barPct = bar !== undefined ? clampPct(bar, max) : null;
  const state = bar === undefined ? "neutral" : passes ? "pass" : "fail";

  const label =
    bar === undefined
      ? `score ${formatScore(value)}`
      : `score ${formatScore(value)}, bar ${formatScore(bar)}, ${
          passes ? "met" : "not met"
        }`;

  return (
    <span
      className={`score-meter score-meter-${state}`}
      role="img"
      aria-label={label}
    >
      <span className="score-meter-num">{formatScore(value)}</span>
      <span className="score-meter-track">
        <span className="score-meter-fill" style={{ width: `${fillPct}%` }} />
        {barPct !== null && (
          <span className="score-meter-tick" style={{ left: `${barPct}%` }} />
        )}
      </span>
      {bar !== undefined && (
        <span className="score-meter-thresh">
          ≥ {formatScore(bar)} {passes ? "✓" : "✗"}
        </span>
      )}
    </span>
  );
}
