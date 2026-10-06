// The data source of the static report. The page does not fetch its run. The
// run is inside the document, in a JSON script element that evaltrack filled.

import { collectDeferred, mapDeferred } from "./deferred";
import { formatBytes } from "./format";
import type { MainlineEntry, Ref, RunHistory, RunRecord } from "./types";

/** Id of the element that holds the report's data. */
export const REPORT_DATA_ID = "evaltrack-data";

/** What a report page embeds: the run, and what the mainline says about it.
 *  Mirrors the backend `ReportData`. */
export interface ReportData {
  run: RunRecord;
  /** The ref the report was asked for by, when it was asked for by one. It
   *  titles the page. */
  via_ref: string | null;
  /** The run the mainline's `baseline` points at, to compare against. Null
   *  when there is none, or it is the run itself. */
  baseline: RunRecord | null;
  /** The refs pointing at the run. */
  refs: Ref[];
  history: RunHistory;
  mainline: MainlineEntry | null;
  /** Why the mainline was not read. `history` is then empty and `baseline`
   *  null, and the page says so. */
  mainline_error: string | null;
  /** Project `{pr}` link template, so that PR numbers are links. */
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

/** Parse the text of the report's data element. It checks only what can go
 *  wrong in practice: a page with nothing in the slot, and text that is not a
 *  report. The rest is taken as written, because one version of evaltrack
 *  writes the page and its data into one file. */
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
  return parsed as unknown as ReportData;
}

/** The report data embedded in `doc`, which is the page's own document. */
export function readEmbeddedReport(doc: Document): ReportData {
  const el = doc.getElementById(REPORT_DATA_ID);
  return parseReportData(el?.textContent);
}

/** How many cases have a value the report left out, across both runs. A case
 *  counts once however many of its values are missing, and once for both
 *  runs, since the comparison shows it as one row. */
export function countCasesWithValuesLeftOut(data: ReportData): number {
  const cases = new Set<string>();
  for (const run of [data.run, data.baseline]) {
    if (!run) continue;
    for (const env of collectDeferred(run)) {
      cases.add(`${env.test}\u0000${env.case}`);
    }
  }
  return cases.size;
}

/** `value` with each value the report left out replaced by text a pane can
 *  show in its place: the first characters, and how much there was. A pane
 *  renders an object as a tree. Without this, it shows the fields of the
 *  envelope. */
export function showPreviewsOfValuesLeftOut<T>(value: T): T {
  return mapDeferred(
    value,
    (env) =>
      `${env.preview}… [${formatBytes(env.size)} in all, left out of the report]`,
  );
}
