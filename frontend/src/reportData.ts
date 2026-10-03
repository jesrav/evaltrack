// The static report's data source. The report page carries its run inside the
// document, in a JSON script element the CLI filled, instead of fetching it.

import { collectDeferred, readDeferredEnvelope } from "./deferred";
import { formatBytes } from "./format";
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

/** Parse the text of the report's data element. Only what can go wrong in
 *  practice is checked: a page with nothing in the slot, and text that is not
 *  a report. The rest is taken as written, since the page and its data are
 *  put into one file by one version of evaltrack. */
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
    !isRecord(parsed.run.run) ||
    !isRecord(parsed.run.run.tests)
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

/** `value` with each value the report left out replaced by text a pane can
 *  show in its place: the first characters, and how much there was. A pane
 *  renders an object as a tree, so the envelope itself would show as its
 *  fields. Returns the same reference when nothing was replaced. */
export function showPreviewsOfValuesLeftOut<T>(value: T): T {
  const walk = (v: unknown): unknown => {
    if (typeof v !== "object" || v === null) return v;
    const env = readDeferredEnvelope(v);
    if (env) {
      return `${env.preview}… [${formatBytes(env.size)} in all, left out of the report]`;
    }
    if (Array.isArray(v)) {
      const items = v.map(walk);
      return items.every((item, i) => item === v[i]) ? v : items;
    }
    let changed = false;
    const out: Record<string, unknown> = {};
    for (const [k, item] of Object.entries(v)) {
      const next = walk(item);
      changed = changed || next !== item;
      out[k] = next;
    }
    return changed ? out : v;
  };
  return walk(value) as T;
}
