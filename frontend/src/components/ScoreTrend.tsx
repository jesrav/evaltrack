// One case's score across the promoted runs, drawn under that score in the case
// drawer. It shows whether the eval is drifting, which the pass/fail history
// cannot, because a case can slide toward its score bar for months and still
// pass every run.
//
// One panel per case and score, never a line averaged over the cases. The score
// bar is per case, so an averaged line can sit above it while a case under it
// goes unseen. It is drawn beside the value it plots, so the score and its
// history are read together.

import { useState } from "react";

import { formatPlural, formatScore } from "../format";
import {
  TREND_VIEW,
  formatScoreDelta,
  hasPromotedHistory,
  layoutTrendPanel,
  describePanel,
  describePoint,
  formatPointLabel,
  getPromotedRange,
  computeTrendChange,
  computeTrendDomain,
  computeTrendPlotEdges,
  buildTrendWindow,
} from "../scoreTrend";
import type { CaseTrend } from "../scoreTrend";

/** Radius of a point, and of the one the reader is on. */
const DOT_R = 4;
const DOT_R_ACTIVE = 6;

/** The trend for one of a case's scores, or a note in its place.
 *
 *  A score the mainline has not carried twice yet gets the note. One promoted
 *  run is a dot, and an empty box looks like a chart that failed to draw. */
export function ScoreTrendPanel({ trend }: { trend: CaseTrend | undefined }) {
  if (!trend || !hasPromotedHistory(trend.points)) {
    return (
      <p className="trend-empty">
        No score history on mainline yet. A trend needs two promoted runs that
        recorded this case under one <code>eval_version</code>.
      </p>
    );
  }
  return <TrendPanel trend={trend} />;
}

/** One case's line, oldest promoted run on the left.
 *
 *  The panel works like a stat tile that scrubs. The header carries the value
 *  of whichever point the reader is on, and the line under the plot names the
 *  run it came from. With nothing hovered or focused, both show the newest
 *  point, so the panel says something on its own. */
function TrendPanel({ trend }: { trend: CaseTrend }) {
  // Hover and focus are transient. A pin survives them, which is the only way
  // to read a point on a touch screen.
  const [reached, setReached] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const { score, bar } = trend;
  // Windowed once, then every helper reads the same points, so no number on the
  // panel can be measured over a run the line does not show.
  const { points, hidden } = buildTrendWindow(trend.points);
  const domain = computeTrendDomain(points, bar);
  const layout = layoutTrendPanel(points, domain, bar);
  const activeIndex = pinned ?? reached;
  const active = activeIndex != null ? layout.points[activeIndex] : undefined;
  const shown = active ?? layout.points.at(-1);
  const change = computeTrendChange(points);
  const promoted = points.filter((p) => !p.off_mainline).length;
  const range = getPromotedRange(points);
  const edges = computeTrendPlotEdges();
  const toggle = (i: number) => setPinned((p) => (p === i ? null : i));
  return (
    <figure className="trend-panel">
      <figcaption className="trend-panel-head">
        <span className="trend-panel-label">
          <span
            title={
              hidden > 0
                ? `Not drawn: ${formatPlural(hidden, "older run")}.`
                : undefined
            }
          >
            {formatPlural(promoted, "promoted run")}
          </span>
          {bar != null && (
            <span
              className="trend-bar-key"
              title="The score bar this eval declares. A case passes when its score reaches the bar."
            >
              <svg
                className="trend-bar-swatch"
                viewBox="0 0 18 2"
                aria-hidden="true"
              >
                <line className="trend-bar-rule" x1="0" y1="1" x2="18" y2="1" />
              </svg>
              bar {formatScore(bar)}
            </span>
          )}
        </span>
        {/* Nothing when the score has not moved. The flat line says it, and a
            bare "0" beside the value only looks like a second number. */}
        {change != null && change !== 0 && (
          <span
            className="trend-delta"
            title="Change from the oldest point on this panel to the newest."
          >
            {formatScoreDelta(change)}
          </span>
        )}
        <span className="trend-value">
          {shown ? formatScore(shown.point.value) : "—"}
        </span>
      </figcaption>
      <div className="trend-plot">
        <div className="trend-axis" aria-hidden="true">
          <span style={{ top: `${edges.top}%` }}>
            {formatScore(domain.max)}
          </span>
          <span style={{ top: `${edges.bottom}%` }}>
            {formatScore(domain.min)}
          </span>
        </div>
        <svg
          className={`trend-chart${hidden > 0 ? " truncated" : ""}`}
          viewBox={`0 0 ${TREND_VIEW.w} ${TREND_VIEW.h}`}
          // A group rather than an image, so the label describes the whole panel
          // and the points inside it stay reachable rather than being flattened
          // away.
          role="group"
          aria-label={describePanel(points, score, bar)}
        >
          {layout.barY != null && (
            <line
              className="trend-bar-rule"
              x1={2}
              x2={TREND_VIEW.w - 2}
              y1={layout.barY}
              y2={layout.barY}
            />
          )}
          {layout.points.map((p, i) =>
            p.highY === p.lowY ? null : (
              <line
                className="trend-spread"
                key={`spread-${i}`}
                x1={p.x}
                x2={p.x}
                y1={p.highY}
                y2={p.lowY}
              />
            ),
          )}
          {active && (
            <line
              className="trend-cursor"
              x1={active.x}
              x2={active.x}
              y1={0}
              y2={TREND_VIEW.h}
            />
          )}
          {layout.mainlinePath !== "" && (
            <path className="trend-line" d={layout.mainlinePath} />
          )}
          {layout.overlayPath !== "" && (
            <path className="trend-line overlay" d={layout.overlayPath} />
          )}
          {layout.points.map((p, i) => (
            <circle
              key={`dot-${i}`}
              className={
                `trend-dot${p.point.off_mainline ? " overlay" : ""}` +
                (i === activeIndex ? " active" : "")
              }
              cx={p.x}
              cy={p.y}
              r={i === activeIndex ? DOT_R_ACTIVE : DOT_R}
            />
          ))}
          {/* Full-height bands, so a point is reachable without landing on the
              dot itself. */}
          {layout.points.map((p, i) => (
            <rect
              key={`hit-${i}`}
              className="trend-hit"
              x={p.bandX}
              y={0}
              width={p.bandW}
              height={TREND_VIEW.h}
              tabIndex={0}
              role="button"
              aria-pressed={pinned === i}
              aria-label={describePoint(p.point)}
              onMouseEnter={() => setReached(i)}
              onMouseLeave={() => setReached(null)}
              onFocus={() => setReached(i)}
              onBlur={() => setReached(null)}
              onClick={() => toggle(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(i);
                }
              }}
            />
          ))}
        </svg>
      </div>
      <p className={`trend-readout${pinned != null ? " pinned" : ""}`}>
        {shown && (
          <>
            <span className="trend-run" title={describePoint(shown.point)}>
              {formatPointLabel(shown.point)}
            </span>
            {shown.point.commit && (
              <code className="trend-commit">
                {shown.point.commit.slice(0, 8)}
              </code>
            )}
          </>
        )}
        {range && (
          <span
            className="trend-range"
            title="What the left and the right of the plot stand for."
          >
            {range.first} → {range.last}
          </span>
        )}
      </p>
    </figure>
  );
}
