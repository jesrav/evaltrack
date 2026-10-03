// The static report's data source. The report page carries its run inside the
// document, in a JSON script element the CLI filled, instead of fetching it.

import { collectDeferred } from "./deferred";
import type { MainlineEntry, Ref, RunHistory, RunRecord } from "./types";

/** Id of the element the CLI writes the report's data into. */
export const REPORT_DATA_ID = "evaltrack-data";

/** A run, and the ref it was reached by, when it was reached by one. */
export interface NamedRun {
  run: RunRecord;
  via: string | null;
}

/** What a report page embeds. Mirrors the backend `ReportData`. `refs` are
 *  the refs pointing at the run. `history` and `mainline` are measured over
 *  the mainline the generator chose. */
export interface ReportData {
  run: NamedRun;
  against: NamedRun | null;
  /** Why a comparison that was asked for is not in the page. */
  against_error: string | null;
  refs: Ref[];
  history: RunHistory;
  mainline: MainlineEntry | null;
  /** Why the history could not be read, when the mainline was unreachable.
   *  `history` is then empty and the page says so. */
  history_error: string | null;
  /** Project `{pr}` link template, so PR numbers link as in the dashboard. */
  pr_url_template: string | null;
  generated_at: string;
  generated_by: string;
}

/** A page whose data is not the embedded report it claims to be. The message
 *  says what is wrong with it, for the page to show in place of the run. */
export class ReportDataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReportDataError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function holdsRun(value: unknown): boolean {
  return isRecord(value) && isRecord(value.run) && isRecord(value.run.tests);
}

/** Parse the text of the report's data element. The shape check stops at what
 *  the page reads first, since a run is too large to validate in full here and
 *  the CLI wrote it from the same models. */
export function parseReportData(text: string | null | undefined): ReportData {
  if (text === null || text === undefined || text.trim() === "") {
    throw new ReportDataError(
      "This page carries no report data. It was not written by `evaltrack report`, or the data was stripped from it.",
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    throw new ReportDataError(
      `The report data does not parse as JSON: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  if (!isRecord(parsed) || !holdsRun(parsed.run)) {
    throw new ReportDataError(
      "The report data does not hold a run. It was not written by this version of `evaltrack report`.",
    );
  }
  const against = parsed.against;
  if (against !== null && against !== undefined && !holdsRun(against)) {
    throw new ReportDataError(
      "The report's comparison run is not a run. It was not written by this version of `evaltrack report`.",
    );
  }
  const data = parsed as unknown as ReportData;
  // Older or hand-made pages can leave the optional parts out; each has a
  // meaning for "absent".
  return {
    run: { run: data.run.run, via: data.run.via ?? null },
    against: data.against
      ? { run: data.against.run, via: data.against.via ?? null }
      : null,
    against_error:
      typeof data.against_error === "string" ? data.against_error : null,
    refs: Array.isArray(data.refs) ? data.refs : [],
    history: isRecord(data.history)
      ? data.history
      : { reliability: {}, score_history: {} },
    mainline: data.mainline ?? null,
    history_error:
      typeof data.history_error === "string" ? data.history_error : null,
    pr_url_template:
      typeof data.pr_url_template === "string" ? data.pr_url_template : null,
    generated_at:
      typeof data.generated_at === "string" ? data.generated_at : "",
    generated_by:
      typeof data.generated_by === "string" ? data.generated_by : "",
  };
}

/** The report data embedded in `doc`, which is the page's own document. */
export function readEmbeddedReport(doc: Document): ReportData {
  const el = doc.getElementById(REPORT_DATA_ID);
  return parseReportData(el?.textContent);
}

/** How many cases have a value the report left out, across both runs. A case
 *  counts once however many of its values are missing, and once for both
 *  sides of a comparison, since the page shows it as one row. */
export function countCasesWithValuesLeftOut(data: ReportData): number {
  const cases = new Set<string>();
  for (const named of [data.run, data.against]) {
    if (!named) continue;
    for (const env of collectDeferred(named.run)) {
      cases.add(`${env.test}\u0000${env.case}`);
    }
  }
  return cases.size;
}
