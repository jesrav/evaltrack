// The lines of the reliability column's info popover.

import { pickNoun, formatPlural, formatRatePct } from "./format";
import type { CaseReliability } from "./types";

/** One line of the note, an optional bold lead-in and then the facts. */
export interface NoteLine {
  label: string | null;
  text: string;
}

/** The note as short lines, one fact each, so it scans in a narrow popover.
 *
 *  `sparkMaxRuns` is how many mainline runs the bar strip draws. A pool larger
 *  than that gets a line saying so, so the strip and the pooled rate cannot look
 *  contradictory.
 *
 *  Pure and exported, so tests check the wording of each state rather than a
 *  rendered popover. */
export function buildReliabilityNoteLines(
  rel: CaseReliability,
  sparkMaxRuns: number,
): NoteLine[] {
  const lines: NoteLine[] = [];
  const target = rel.target != null ? Math.round(rel.target * 100) : null;
  lines.push({
    label: "Mainline",
    text:
      `${rel.pooled_passes}/${rel.pooled_attempts} ${pickNoun(rel.pooled_attempts, "attempt", "attempts")} ` +
      `across ${formatPlural(rel.pooled_runs, "run")}` +
      (target != null ? ` · target ${target}%` : ""),
  });
  if (rel.lower_bound != null) {
    lines.push({
      label: "Lower bound",
      // Rates render floored, so the lower bound is never rounded up past
      // itself. The verdict below is compared on the raw values, so a bound
      // that displays as the target is not called met.
      text:
        `${formatRatePct(rel.lower_bound)}% at 95% confidence` +
        describeBoundVerdict(rel),
    });
  }
  if (rel.points.filter((p) => !p.off_mainline).length > sparkMaxRuns) {
    lines.push({
      label: null,
      text: `The bars show the ${sparkMaxRuns} most recent runs.`,
    });
  }
  if (rel.points.some((p) => p.off_mainline)) {
    lines.push({
      label: null,
      text: "This run is not on mainline. Its attempts are the outlined bars.",
    });
  }
  return lines;
}

/** What the lower bound says about the target, or nothing when no target is set. */
function describeBoundVerdict(rel: CaseReliability): string {
  if (rel.target == null || rel.lower_bound == null) return "";
  if (rel.below_target) return ". The rate is below the target";
  // The chip reads the rate against the target alone, so this line is the only
  // place a rate the samples cannot yet confirm says so.
  if (rel.lower_bound < rel.target) {
    return ". Too few samples to confirm the target yet";
  }
  return ". The target is met";
}
