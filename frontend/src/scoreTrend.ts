// Layout maths for the score-trend chart. It draws one case's score across the
// promoted runs as one panel beside that score in the case drawer.
//
// Pure and exported, so tests check the geometry and the wording rather than a
// rendered chart.

import { formatPlural, formatScore } from "./format";
import type { ScoreHistory, ScoreHistoryPoint } from "./types";

/** Promoted runs a panel draws. Past this the points sit closer than a finger
 *  and the panel stops saying anything. The oldest fall off the left edge, and
 *  the caption says so. */
export const TREND_MAX_POINTS = 24;

/** The points a panel draws, and the promoted runs that fell off its left
 *  edge. */
interface TrendWindow {
  points: ScoreHistoryPoint[];
  hidden: number;
}

/** Cut a case's score history down to what a panel draws.
 *
 *  Every number a panel shows is measured over these points, so the axis, the
 *  header delta and the caption cannot describe runs that are not on the line.
 *  The viewed run is not promoted, so it does not count against the cap and
 *  is kept whatever the cap drops. */
export function buildTrendWindow(points: ScoreHistoryPoint[]): TrendWindow {
  const promoted = getPromotedPoints(points);
  const shown = promoted.slice(-TREND_MAX_POINTS);
  return {
    points: [...shown, ...points.filter((p) => p.off_mainline)],
    hidden: promoted.length - shown.length,
  };
}

/** The panel's SVG coordinate space, sized so it renders near 1:1 across the
 *  drawer. Scaled far up, a 2px line and a 4px dot turn into slabs. `pad` keeps
 *  a dot and its ring inside the box instead of clipped at the edge, and the
 *  same numbers become percentages to place the two axis labels beside the
 *  plot. */
export const TREND_VIEW = { w: 640, h: 150, padX: 12, padY: 16 } as const;

/** Where the plot's top and bottom sit, as a percentage of the panel height, so
 *  the axis labels line up with the value they name. */
export function computeTrendPlotEdges(): { top: number; bottom: number } {
  return {
    top: (TREND_VIEW.padY / TREND_VIEW.h) * 100,
    bottom: ((TREND_VIEW.h - TREND_VIEW.padY) / TREND_VIEW.h) * 100,
  };
}

/** The y-range a score's panels share. */
interface TrendDomain {
  min: number;
  max: number;
}

/** One point placed in the panel's coordinate space.
 *
 *  `lowY`/`highY` bound the run's repeated attempts and equal `y` when it
 *  scored the case once. `bandX`/`bandW` are the hit target, a full-height slice
 *  of the panel, so a point is reachable without landing on a 3px dot. */
interface PlacedPoint {
  x: number;
  y: number;
  lowY: number;
  highY: number;
  bandX: number;
  bandW: number;
  point: ScoreHistoryPoint;
}

interface PanelLayout {
  points: PlacedPoint[];
  /** Polyline through the promoted runs. Empty for a single point. */
  mainlinePath: string;
  /** The segment joining the last promoted run to the viewed one. Empty when
   *  the viewed run is not on the panel. */
  overlayPath: string;
  /** Where the score bar rule sits, or null when the eval declares no bar. */
  barY: number | null;
}

/** A round number near a quarter of `span`, so the two axis labels are values a
 *  reader can hold in their head (0.2, 0.05) rather than the data's own edges. */
function computeNiceStep(span: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(span / 4));
  const steps = [1, 2, 5, 10];
  const normalized = span / 4 / magnitude;
  return (steps.find((s) => normalized <= s) ?? 10) * magnitude;
}

function snap(value: number, step: number, direction: -1 | 1): number {
  const units = value / step;
  const snapped = direction < 0 ? Math.floor(units) : Math.ceil(units);
  return Math.round(snapped * step * 1e6) / 1e6;
}

/** The y-range for the points a panel draws. It covers every value, every
 *  attempt spread, and the bar, with room left around them and round numbers at
 *  the edges.
 *
 *  The bar is always inside it, because a line approaching the bar is the thing
 *  the chart is drawn for. A flat history still gets a range, so its line
 *  lands mid-panel rather than on an edge. */
export function computeTrendDomain(
  drawn: ScoreHistoryPoint[],
  bar: number | null,
): TrendDomain {
  const values: number[] = [];
  for (const p of drawn) values.push(p.low, p.high);
  if (bar != null) values.push(bar);
  if (values.length === 0) return { min: 0, max: 1 };
  const pad = Math.max(
    (Math.max(...values) - Math.min(...values)) * 0.12,
    0.02,
  );
  const low = Math.min(...values) - pad;
  const high = Math.max(...values) + pad;
  const step = computeNiceStep(high - low);
  return { min: snap(low, step, -1), max: snap(high, step, 1) };
}

function scaleY(value: number, domain: TrendDomain): number {
  const span = domain.max - domain.min || 1;
  const top = TREND_VIEW.padY;
  const height = TREND_VIEW.h - TREND_VIEW.padY * 2;
  return top + (1 - (value - domain.min) / span) * height;
}

// How much wider the step into the viewed run is than a step between two
// promoted ones. The gap is what says the last point is not promoted.
const OVERLAY_STEP = 1.7;

/** Place one case's drawn points left to right, oldest to newest.
 *
 *  Promoted runs are evenly spaced. The viewed run, when the trend carries one,
 *  sits after a wider gap, because it is not promoted and does not belong on
 *  the mainline axis. */
export function layoutTrendPanel(
  drawn: ScoreHistoryPoint[],
  domain: TrendDomain,
  bar: number | null,
): PanelLayout {
  const promoted = drawn.filter((p) => !p.off_mainline);
  const overlay = drawn.filter((p) => p.off_mainline);
  const all = [...promoted, ...overlay];

  const left = TREND_VIEW.padX;
  const width = TREND_VIEW.w - TREND_VIEW.padX * 2;
  // Steps rather than points, because the overlay's own step is wider and the
  // last point then lands on the right edge whether or not there is one.
  const steps =
    promoted.length > 0
      ? promoted.length - 1 + (overlay.length > 0 ? OVERLAY_STEP : 0)
      : Math.max(all.length - 1, 0);
  const step = steps > 0 ? width / steps : 0;

  const placed: PlacedPoint[] = all.map((point, i) => {
    // A lone point has nowhere to go but the middle.
    const offset =
      promoted.length === 0 || i < promoted.length
        ? i
        : promoted.length - 1 + OVERLAY_STEP;
    const x = steps > 0 ? left + offset * step : left + width / 2;
    // Rounded here rather than at each use, so the placed coordinates, the path
    // and the hit bands cannot disagree by a rounding step.
    return {
      x: round(x),
      y: round(scaleY(point.value, domain)),
      lowY: round(scaleY(point.low, domain)),
      highY: round(scaleY(point.high, domain)),
      bandX: 0,
      bandW: 0,
      point,
    };
  });
  // Hit bands split the panel at the midpoints between neighbours, so every
  // pixel belongs to its nearest point and none of them is a pinpoint target.
  placed.forEach((p, i) => {
    const before = placed[i - 1];
    const after = placed[i + 1];
    const start = before ? (before.x + p.x) / 2 : 0;
    const end = after ? (p.x + after.x) / 2 : TREND_VIEW.w;
    p.bandX = start;
    p.bandW = end - start;
  });

  const line = (from: PlacedPoint[]) =>
    from.length > 1
      ? from.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ")
      : "";
  const mainline = placed.slice(0, promoted.length);
  const last = mainline.at(-1);
  const overlayPoint = placed.at(-1);
  return {
    points: placed,
    mainlinePath: line(mainline),
    overlayPath:
      overlay.length > 0 && last && overlayPoint && last !== overlayPoint
        ? line([last, overlayPoint])
        : "",
    barY: bar != null ? scaleY(bar, domain) : null,
  };
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}

/** A signed change between two score values, or "no change" as a plain "0". */
export function formatScoreDelta(delta: number): string {
  const rounded = Math.round(delta * 1000) / 1000;
  if (rounded === 0) return "0";
  return `${rounded > 0 ? "+" : "−"}${formatScore(Math.abs(rounded))}`;
}

/** How far the newest drawn point sits from the oldest one, or null when there
 *  is nothing to compare it against. */
export function computeTrendChange(drawn: ScoreHistoryPoint[]): number | null {
  const first = drawn.at(0);
  const last = drawn.at(-1);
  if (!first || !last || first === last) return null;
  return last.value - first.value;
}

/** The short label under a panel, naming the run a point came from. The PR
 *  is what a reader recognizes, so it leads. The commit follows for a mainline
 *  entry that recorded no PR. */
export function formatPointLabel(point: ScoreHistoryPoint): string {
  if (point.off_mainline) return "this run";
  if (point.pr != null) return `PR ${point.pr}`;
  if (point.commit) return point.commit.slice(0, 8);
  return new Date(point.created_at).toLocaleDateString();
}

/** The full sentence a point is announced and hovered with. */
export function describePoint(point: ScoreHistoryPoint): string {
  const parts = [`${formatPointLabel(point)}: ${formatScore(point.value)}`];
  if (point.off_mainline) {
    parts.push("the run you are viewing, not promoted");
  } else if (point.commit) {
    parts.push(`commit ${point.commit.slice(0, 8)}`);
  }
  if (point.title) parts.push(point.title);
  if (point.attempts > 1) {
    parts.push(
      `mean of ${point.attempts} attempts, ${formatScore(point.low)} to ${formatScore(point.high)}`,
    );
  }
  return parts.join(" · ");
}

/** The panel's text alternative, what the shape says in words.
 *
 *  A reader who cannot see the line gets the same three facts it carries: where
 *  the score started, where it is now, and whether it sits under its bar. The
 *  case is not named, because the pane the panel sits in already is. */
export function describePanel(
  drawn: ScoreHistoryPoint[],
  score: string,
  bar: number | null,
): string {
  const first = drawn.at(0);
  const last = drawn.at(-1);
  if (!first || !last) return `No ${score} recorded.`;
  const promoted = getPromotedPoints(drawn).length;
  const sentences = [`${score} over ${formatPlural(promoted, "promoted run")}`];
  if (first === last) {
    sentences.push(`One point, ${formatScore(last.value)}`);
  } else {
    const change = formatScoreDelta(last.value - first.value);
    sentences.push(
      `From ${formatScore(first.value)} at ${formatPointLabel(first)} ` +
        `to ${formatScore(last.value)} at ${formatPointLabel(last)}, ${change}`,
    );
  }
  if (bar != null) {
    sentences.push(
      last.value < bar
        ? `Under the score bar of ${formatScore(bar)}`
        : `Score bar ${formatScore(bar)}`,
    );
  }
  return `${sentences.join(". ")}.`;
}

/** The promoted runs at each end of the panel's x-axis, so a reader knows what
 *  its left and right stand for. Null when the panel spans no step. */
export function getPromotedRange(
  drawn: ScoreHistoryPoint[],
): { first: string; last: string } | null {
  const promoted = getPromotedPoints(drawn);
  const first = promoted.at(0);
  const last = promoted.at(-1);
  if (!first || !last || first === last) return null;
  return { first: formatPointLabel(first), last: formatPointLabel(last) };
}

function getPromotedPoints(points: ScoreHistoryPoint[]): ScoreHistoryPoint[] {
  return points.filter((p) => !p.off_mainline);
}

/** Whether a history is worth drawing.
 *
 *  One promoted run is a dot, not a trend, and a chart of it claims a history
 *  that does not exist yet. */
export function hasPromotedHistory(points: ScoreHistoryPoint[]): boolean {
  return getPromotedPoints(points).length >= 2;
}

/** One case's history for one score, with the bar the panel rules against. */
export interface CaseTrend {
  score: string;
  bar: number | null;
  points: ScoreHistoryPoint[];
}

/** One case's trends, keyed by score name, so a pane can look up the score it
 *  is drawing. A score the case never recorded is absent. */
export function buildCaseTrends(
  scores: ScoreHistory[] | undefined,
  caseId: string,
): Record<string, CaseTrend> {
  const byScore: Record<string, CaseTrend> = {};
  for (const history of scores ?? []) {
    const forCase = history.cases[caseId];
    if (forCase) {
      byScore[history.score] = {
        score: history.score,
        bar: forCase.bar,
        points: forCase.points,
      };
    }
  }
  return byScore;
}
