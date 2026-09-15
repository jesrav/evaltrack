// The static report's data source. The report page carries its run inside the
// document, in a JSON script element the CLI filled, instead of fetching it.

import type { MainlineEntry, Ref, RunHistory, RunRecord } from "./types";

/** Id of the element the CLI writes the report's data into. */
export const REPORT_DATA_ID = "evaltrack-data";

/** What a report page embeds. Mirrors the backend `ReportData`. `via` and
 *  `against_via` name the ref each run was reached by, when it was one, and
 *  `refs` are the refs pointing at the run. `history` and `mainline` are
 *  measured over the mainline the generator chose. */
export interface ReportData {
  run: RunRecord;
  via: string | null;
  against: RunRecord | null;
  against_via: string | null;
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
  if (
    !isRecord(parsed) ||
    !isRecord(parsed.run) ||
    !isRecord(parsed.run.tests)
  ) {
    throw new ReportDataError(
      "The report data does not hold a run. It was not written by this version of `evaltrack report`.",
    );
  }
  const against = parsed.against;
  if (against !== null && against !== undefined && !isRecord(against)) {
    throw new ReportDataError(
      "The report's comparison run is not a run. It was not written by this version of `evaltrack report`.",
    );
  }
  const data = parsed as unknown as ReportData;
  // Older or hand-made pages can leave the optional parts out; each has a
  // meaning for "absent".
  return {
    run: data.run,
    via: data.via ?? null,
    against: data.against ?? null,
    against_via: data.against_via ?? null,
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
