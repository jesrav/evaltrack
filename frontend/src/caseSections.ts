// Which sections the case inspector shows, and in what order.
//
// A case records only some of these, so a section exists only when the case has
// something to put in it. The order is fixed: what the case asked for, what came
// back, then what the evaluators reported on it. Where a section lands on screen
// is the view's decision, not this module's.

import type { AttemptRecord, CaseRecord, ResultKind } from "./types";
import { hasKind, resultsOfKind } from "./results";

export type CaseSectionId =
  | "input"
  /** The expected value and the produced one together, diff first. */
  | "comparison"
  | "expected"
  | "output"
  | "metadata"
  | "scores"
  | "assertions"
  | "error";

/** Which section holds a result of this kind. Every view that shows evaluator
 *  results groups them by this, so a click on a pill anywhere reaches the
 *  result itself rather than a list it is not in. */
export function sectionOfKind(kind: ResultKind): CaseSectionId {
  return kind === "score" ? "scores" : "assertions";
}

export interface CaseSection {
  id: CaseSectionId;
  label: string;
}

/** Which part of a case a click asks for. `name` picks one score or assertion,
 *  so a click on a pill reaches that pill and not only its list. */
export interface CaseFocus {
  section: CaseSectionId;
  name?: string;
}

/** The sections one case shows, in display order.
 *
 *  `attempt` is the attempt on screen, so a switch of attempt can add or remove
 *  a section. The expected and produced values share one section when the case
 *  has both, because a failed comparison is about the pair. */
export function buildCaseSections(
  c: CaseRecord,
  attempt: AttemptRecord | undefined,
): CaseSection[] {
  const sections: CaseSection[] = [];
  const errored = attempt?.outcome === "errored";
  // An errored attempt reached no verdict, so the output, scores and
  // assertions it did record are dropped in favour of the error.
  const produced = errored ? undefined : attempt;
  const hasOutput = produced !== undefined && produced.output !== undefined;
  const hasExpected = c.expected_output != null;

  if (c.inputs != null) sections.push({ id: "input", label: "Input" });
  if (hasExpected && hasOutput) {
    sections.push({ id: "comparison", label: "Expected and output" });
  } else if (hasExpected) {
    sections.push({ id: "expected", label: "Expected" });
  } else if (hasOutput) {
    sections.push({ id: "output", label: "Output" });
  }
  if (c.metadata != null) sections.push({ id: "metadata", label: "Metadata" });
  if (produced && hasKind(produced.results, "score")) {
    sections.push({ id: "scores", label: "Scores" });
  }
  if (produced && hasKind(produced.results, "assertion")) {
    sections.push({ id: "assertions", label: "Assertions" });
  }
  if (errored) sections.push({ id: "error", label: "Error" });
  return sections;
}

/** The section a click lands on, or null when the case has no such section.
 *
 *  A click on the expected cell or the output cell falls through to the shared
 *  section, because the two values merge into one whenever both exist. */
export function findFocusedSection(
  sections: CaseSection[],
  focus: CaseFocus | undefined,
): CaseSectionId | null {
  if (!focus) return null;
  const has = (id: CaseSectionId) => sections.some((s) => s.id === id);
  if (has(focus.section)) return focus.section;
  const paired = focus.section === "expected" || focus.section === "output";
  if (paired && has("comparison")) return "comparison";
  return null;
}

/** Key of the element a click scrolls to. Sections and the named evaluators
 *  inside them share one namespace, so the pane needs one set of anchors. */
export function buildAnchorKey(section: CaseSectionId, name?: string): string {
  return name === undefined ? section : `${section}:${name}`;
}

/** The anchor a click scrolls to, or null when there is nothing to scroll to.
 *
 *  A named evaluator that the shown attempt does not carry falls back to its
 *  section heading, so switching to an attempt that lost an evaluator still
 *  lands near what was clicked. */
export function focusAnchor(
  sections: CaseSection[],
  attempt: AttemptRecord | undefined,
  focus: CaseFocus | undefined,
): string | null {
  const section = findFocusedSection(sections, focus);
  if (section === null || focus === undefined) return null;
  if (focus.name === undefined) return buildAnchorKey(section);
  const named =
    section === "scores"
      ? resultsOfKind(attempt?.results, "score")
      : section === "assertions"
        ? resultsOfKind(attempt?.results, "assertion")
        : undefined;
  if (named && focus.name in named) return buildAnchorKey(section, focus.name);
  return buildAnchorKey(section);
}
