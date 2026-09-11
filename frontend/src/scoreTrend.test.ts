import { describe, it, expect } from "vitest";

import {
  TREND_MAX_POINTS,
  TREND_VIEW,
  buildCaseTrends,
  formatScoreDelta,
  hasPromotedHistory,
  layoutTrendPanel,
  describePanel,
  describePoint,
  formatPointLabel,
  getPromotedRange,
  computeTrendChange,
  computeTrendDomain,
  buildTrendWindow,
} from "./scoreTrend";
import { buildScorePoint } from "./test-support";
import type { ScoreHistoryPoint, ScoreHistory } from "./types";

const history = (
  values: number[],
  opts: { overlay?: number } = {},
): ScoreHistoryPoint[] => [
  ...values.map((v) => buildScorePoint(v)),
  ...(opts.overlay != null
    ? [buildScorePoint(opts.overlay, { off_mainline: true })]
    : []),
];

const left = TREND_VIEW.padX;
const right = TREND_VIEW.w - TREND_VIEW.padX;

// The range decides what the shape says. Too tight and every wobble looks like
// a crash. Without the bar in it, the line the chart exists to compare against
// is off screen.
describe("trend domain", () => {
  it("holds every value with room around them", () => {
    const d = computeTrendDomain(history([0.4, 0.6]), null);
    expect(d.min).toBeLessThan(0.4);
    expect(d.max).toBeGreaterThan(0.6);
  });

  it("always holds the score bar, so an approach to it is visible", () => {
    const d = computeTrendDomain(history([0.8, 0.9]), 0.3);
    expect(d.min).toBeLessThan(0.3);
  });

  it("covers the spread of a run that scored the case more than once", () => {
    const d = computeTrendDomain(
      [buildScorePoint(0.5, { low: 0.1, high: 0.9 })],
      null,
    );
    expect(d.min).toBeLessThan(0.1);
    expect(d.max).toBeGreaterThan(0.9);
  });

  it("gives a flat history a range, so its line lands mid-panel", () => {
    const d = computeTrendDomain(history([0.5, 0.5, 0.5]), null);
    expect(d.max).toBeGreaterThan(d.min);
    expect((0.5 - d.min) / (d.max - d.min)).toBeCloseTo(0.5);
  });
});

describe("trend panel layout", () => {
  it("runs oldest to newest, filling the plot", () => {
    const domain = { min: 0, max: 1 };
    const { points } = layoutTrendPanel(history([0.2, 0.5, 0.8]), domain, null);
    expect(points.map((p) => p.x)).toEqual([left, (left + right) / 2, right]);
    // y grows downward, so a rising score climbs the panel.
    expect(points[0]!.y).toBeGreaterThan(points[2]!.y);
  });

  it("puts a lone point in the middle rather than on an edge", () => {
    const { points, mainlinePath } = layoutTrendPanel(
      history([0.5]),
      { min: 0, max: 1 },
      null,
    );
    expect(points[0]!.x).toBe(TREND_VIEW.w / 2);
    expect(mainlinePath).toBe(""); // one point is not a line
  });

  it("sets the viewed run apart from the promoted runs", () => {
    const points = history([0.4, 0.6], { overlay: 0.2 });
    const { points: placed, overlayPath } = layoutTrendPanel(
      points,
      { min: 0, max: 1 },
      null,
    );
    const [first, second, overlay] = placed;
    // The step into the viewed run is wider than a step between two promoted
    // runs, because it is not promoted.
    expect(overlay!.x - second!.x).toBeGreaterThan(second!.x - first!.x);
    expect(overlay!.x).toBe(right); // the newest point still anchors the right
    expect(overlayPath).not.toBe("");
  });

  it("draws every point it is given, promoted runs then the viewed run", () => {
    const values = Array.from({ length: TREND_MAX_POINTS }, (_, i) => i / 100);
    const drawn = buildTrendWindow(history(values, { overlay: 0.5 })).points;
    const layout = layoutTrendPanel(drawn, { min: 0, max: 1 }, null);
    expect(layout.points).toHaveLength(TREND_MAX_POINTS + 1);
    expect(layout.points.at(-1)!.point.off_mainline).toBe(true);
    expect(layout.points.at(-1)!.x).toBe(right);
    expect(layout.overlayPath).not.toBe("");
  });

  it("gives every point a hit band, so none is a pinpoint target", () => {
    const { points } = layoutTrendPanel(
      history([0.2, 0.5, 0.8]),
      { min: 0, max: 1 },
      null,
    );
    // The bands tile the whole panel and never overlap.
    expect(points[0]!.bandX).toBe(0);
    for (const [i, p] of points.entries()) {
      const next = points[i + 1];
      if (next) expect(p.bandX + p.bandW).toBeCloseTo(next.bandX);
    }
    expect(points.at(-1)!.bandX + points.at(-1)!.bandW).toBe(TREND_VIEW.w);
  });

  it("places the bar rule at the bar's own height, or nowhere", () => {
    const domain = { min: 0, max: 1 };
    const points = history([0.5]);
    const mid = layoutTrendPanel(points, domain, 0.5);
    expect(mid.barY).toBeCloseTo(mid.points[0]!.y);
    expect(layoutTrendPanel(points, domain, null).barY).toBeNull();
  });
});

const promotedValues = (n: number) => Array.from({ length: n }, () => 0.5);

// The window is what every other helper measures over, so a run it keeps or
// drops decides what the axis, the header and the caption say.
describe("trend window", () => {
  it("keeps a history that fits, with nothing hidden", () => {
    const points = history(promotedValues(5));
    const drawn = buildTrendWindow(points);
    expect(drawn.points).toEqual(points);
    expect(drawn.hidden).toBe(0);
  });

  it("keeps a history of exactly the cap", () => {
    const drawn = buildTrendWindow(history(promotedValues(TREND_MAX_POINTS)));
    expect(drawn.points).toHaveLength(TREND_MAX_POINTS);
    expect(drawn.hidden).toBe(0);
  });

  it("keeps one promoted run on its own", () => {
    const drawn = buildTrendWindow(history([0.5]));
    expect(drawn.points).toHaveLength(1);
    expect(drawn.hidden).toBe(0);
  });

  it("drops the oldest promoted runs past the cap and counts them", () => {
    const values = Array.from(
      { length: TREND_MAX_POINTS + 5 },
      (_, i) => i / 100,
    );
    const drawn = buildTrendWindow(history(values));
    expect(drawn.points).toHaveLength(TREND_MAX_POINTS);
    expect(drawn.hidden).toBe(5);
    expect(drawn.points[0]!.value).toBeCloseTo(values[5]!);
    expect(drawn.points.at(-1)!.value).toBeCloseTo(values.at(-1)!);
  });

  it("keeps the viewed run last, whatever the cap drops", () => {
    const drawn = buildTrendWindow(
      history(promotedValues(TREND_MAX_POINTS + 5), { overlay: 0.4 }),
    );
    // The viewed run is not promoted, so it does not spend a slot.
    expect(drawn.points).toHaveLength(TREND_MAX_POINTS + 1);
    expect(drawn.hidden).toBe(5);
    expect(drawn.points.at(-1)!.off_mainline).toBe(true);
  });
});

// The panel used to read its numbers off the whole history while drawing only
// the last 24 of it. With older runs at a far lower score, that stretched the
// axis down to a point off the plot and measured the header delta from a run
// nobody could see.
describe("a history longer than the panel draws", () => {
  const points = [
    ...Array.from({ length: 6 }, (_, i) => buildScorePoint(0.1, { pr: i + 1 })),
    ...Array.from({ length: TREND_MAX_POINTS }, (_, i) =>
      buildScorePoint(i === TREND_MAX_POINTS - 1 ? 0.95 : 0.9, {
        pr: i + 7,
      }),
    ),
  ];
  const drawn = buildTrendWindow(points);

  it("hides the six oldest runs and says so", () => {
    expect(drawn.points).toHaveLength(TREND_MAX_POINTS);
    expect(drawn.hidden).toBe(6);
    expect(drawn.points[0]!.pr).toBe(7);
  });

  it("scales the axis to the drawn points", () => {
    const d = computeTrendDomain(drawn.points, null);
    expect(d.min).toBeGreaterThan(0.5);
    expect(d.max).toBeGreaterThanOrEqual(0.95);
  });

  it("reads the change between the drawn ends", () => {
    expect(computeTrendChange(drawn.points)).toBeCloseTo(0.05);
  });

  it("describes the drawn history", () => {
    expect(describePanel(drawn.points, "compression", null)).toBe(
      `compression over ${TREND_MAX_POINTS} promoted runs. ` +
        "From 0.9 at PR 7 to 0.95 at PR 30, +0.05.",
    );
  });

  it("names the ends of the plot from the drawn points", () => {
    expect(getPromotedRange(drawn.points)).toEqual({
      first: "PR 7",
      last: "PR 30",
    });
  });

  it("still ends on the viewed run when there is one", () => {
    const withOverlay = buildTrendWindow([
      ...points,
      buildScorePoint(0.4, { off_mainline: true }),
    ]);
    expect(computeTrendChange(withOverlay.points)).toBeCloseTo(-0.5);
    expect(describePanel(withOverlay.points, "compression", null)).toBe(
      `compression over ${TREND_MAX_POINTS} promoted runs. ` +
        "From 0.9 at PR 7 to 0.4 at this run, −0.5.",
    );
    // The caption names promoted runs, so it ends on the newest one drawn.
    expect(getPromotedRange(withOverlay.points)?.last).toBe("PR 30");
  });
});

describe("trend wording", () => {
  it("names the run a point came from", () => {
    expect(formatPointLabel(buildScorePoint(0.5, { pr: 108 }))).toBe("PR 108");
    expect(
      formatPointLabel(buildScorePoint(0.5, { commit: "abcdef1234" })),
    ).toBe("abcdef12");
    expect(formatPointLabel(buildScorePoint(0.5, { off_mainline: true }))).toBe(
      "this run",
    );
  });

  it("says a run's spread, so a wide run is not read as a move", () => {
    const text = describePoint(
      buildScorePoint(0.5, { pr: 12, low: 0.2, high: 0.8, attempts: 2 }),
    );
    expect(text).toContain("mean of 2 attempts, 0.2 to 0.8");
  });

  it("describes the shape of a panel in words", () => {
    const text = describePanel(
      [buildScorePoint(0.65, { pr: 101 }), buildScorePoint(0.42, { pr: 108 })],
      "compression",
      0.5,
    );
    expect(text).toBe(
      "compression over 2 promoted runs. " +
        "From 0.65 at PR 101 to 0.42 at PR 108, −0.23. " +
        "Under the score bar of 0.5.",
    );
  });

  it("formats a change with its direction", () => {
    expect(formatScoreDelta(0.25)).toBe("+0.25");
    expect(formatScoreDelta(-0.25)).toBe("−0.25");
    expect(formatScoreDelta(0)).toBe("0");
  });

  it("has no change to report for a single point", () => {
    expect(computeTrendChange(history([0.5]))).toBeNull();
  });
});

/** `bar` applies to every case given, unless `bars` names one for a case. */
const historyFor = (
  score: string,
  cases: Record<string, ScoreHistoryPoint[]>,
  bar: number | null = null,
  bars: Record<string, number | null> = {},
): ScoreHistory => ({
  score,
  cases: Object.fromEntries(
    Object.entries(cases).map(([name, points]) => [
      name,
      { bar: bars[name] ?? bar, eval_version: null, points },
    ]),
  ),
});

describe("one case's trends", () => {
  it("keeps the history of that case only, keyed by score", () => {
    const byScore = buildCaseTrends(
      [
        historyFor(
          "compression",
          { a: history([0.4]), b: history([0.9]) },
          0.3,
        ),
        historyFor("accuracy", { b: history([0.5]) }),
      ],
      "a",
    );
    expect(Object.keys(byScore)).toEqual(["compression"]);
    expect(byScore.compression?.bar).toBe(0.3);
    expect(byScore.compression?.points).toHaveLength(1);
  });

  it("rules each case against its own bar", () => {
    // A case whose segment ended at an older run keeps that run's bar, so the
    // panel must read the bar off the case and not off the score.
    const scores = [
      historyFor(
        "compression",
        { a: history([0.95]), b: history([0.55]) },
        0.9,
        { b: 0.5 },
      ),
    ];
    expect(buildCaseTrends(scores, "a").compression?.bar).toBe(0.9);
    expect(buildCaseTrends(scores, "b").compression?.bar).toBe(0.5);
  });

  it("needs two promoted runs before there is a trend to draw", () => {
    expect(hasPromotedHistory(history([0.5]))).toBe(false);
    expect(hasPromotedHistory(history([0.5], { overlay: 0.4 }))).toBe(false);
    expect(hasPromotedHistory(history([0.5, 0.6]))).toBe(true);
  });
});
