// Which case rows the run view keeps, given the filter controls above it.
//
// The filter reads a case's name and its outcome, nothing else. What a hidden
// row does to the surrounding test, module or banner is the view's decision,
// not this module's.

import type { CaseRecord } from "./types";

export interface CaseFilter {
  /** Keep only the cases that failed or errored. */
  failingOnly: boolean;
  /** Substring of the case id, matched without case. Empty keeps every case. */
  name: string;
}

export const NO_CASE_FILTER: CaseFilter = { failingOnly: false, name: "" };

/** Whether the filter can hide anything. A filter at its defaults keeps every
 *  case, so the view can skip the "some rows are hidden" wording. */
export function isCaseFilterActive(filter: CaseFilter): boolean {
  return filter.failingOnly || filter.name.trim() !== "";
}

/** Whether one case survives the filter. A case passes on its own name and its
 *  own outcome, so a test's verdict never rescues or hides its rows. */
export function matchesCaseFilter(
  name: string,
  result: Pick<CaseRecord, "outcome">,
  filter: CaseFilter,
): boolean {
  if (filter.failingOnly && result.outcome === "passed") return false;
  const needle = filter.name.trim().toLowerCase();
  return needle === "" || name.toLowerCase().includes(needle);
}

/** The surviving `[name, case]` entries, in the order they came in. */
export function filterCases(
  cases: [string, CaseRecord][],
  filter: CaseFilter,
): [string, CaseRecord][] {
  return cases.filter(([name, result]) =>
    matchesCaseFilter(name, result, filter),
  );
}
