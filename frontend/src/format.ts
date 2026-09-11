// Human-friendly formatting helpers shared across views.

import { ApiError } from "./api";

/** Count + noun with a plain "s" plural: `formatPlural(1, "test")` → "1 test". */
export const formatPlural = (n: number, word: string): string =>
  `${n} ${word}${n === 1 ? "" : "s"}`;

export const pickNoun = (n: number, one: string, many: string): string =>
  n === 1 ? one : many;

/** A pass rate in [0, 1] as a percent label without the "%", floored rather
 *  than rounded, so 199/200 never shows as "100" nor 1/250 as "0". Exactly 1
 *  gives "100" and exactly 0 gives "0". Every other value floors to an integer
 *  percent, except where flooring hides real signal at an extreme ("99" hiding
 *  99.5, "0" hiding 0.4). Those get one floored decimal. */
export function formatRatePct(rate: number): string {
  if (rate <= 0) return "0";
  if (rate >= 1) return "100";
  // Floor to permille. The nudge keeps float noise in exact ratios (29/100 →
  // 28.999…) from flooring a true boundary down. The clamp keeps values inside
  // (0, 1) from ever reaching the "0"/"100" extremes.
  const permille = Math.min(Math.floor(rate * 1000 + 1e-9), 999);
  const intPct = Math.floor(permille / 10);
  if (intPct === 99 || intPct === 0) return (permille / 10).toFixed(1);
  return String(intPct);
}

/** Format a byte count as a compact size label, for example "18 B" or
 *  "1.2 MB". Binary units. One decimal only where it still says something
 *  ("1.2 MB", not "12.3 MB"). */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n;
  let i = -1;
  do {
    value /= 1024;
    i += 1;
  } while (value >= 1024 && i < units.length - 1);
  const label =
    value >= 10
      ? String(Math.round(value))
      : (Math.round(value * 10) / 10).toFixed(1).replace(/\.0$/, "");
  return `${label} ${units[i]}`;
}

/** A score value as the dashboard writes it everywhere, at most three decimals
 *  with trailing zeros dropped. */
export function formatScore(value: number): string {
  return String(Math.round(value * 1000) / 1000);
}

/** A task latency in seconds → a compact label, e.g. "820ms" or "2.34s". */
export function formatDuration(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${(Math.round(seconds * 100) / 100).toFixed(2)}s`;
}

/** A signed latency delta in seconds → e.g. "+0.50s" / "−0.12s". */
export function formatDurationDelta(seconds: number): string {
  const sign = seconds >= 0 ? "+" : "−";
  return `${sign}${formatDuration(Math.abs(seconds))}`;
}

/** Why a 422 refused to serve an object, from the server's detail. The detail
 *  already opens with "cannot be read", and the callers front it with the
 *  object's own name, so that opening goes. Null for any other failure. */
function unreadableReason(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  return error.message.replace(/^cannot be read: /, "");
}

/** Banner text for a run that failed to load. A 404 is the everyday case of a
 *  stale link to a run that was deleted or cleaned up, and a 400 is a link whose
 *  id was never one, so both get plain English. A 422 is a run that is still
 *  there but cannot be read, and its detail says why, so that stays. Anything
 *  else keeps its detail, the only clue the reader gets for a server or network
 *  fault. */
export function formatRunLoadMessage(error: unknown, runId: string): string {
  if (error instanceof ApiError && error.status === 404) {
    return `Run ${runId.slice(0, 10)} not found. It may have been deleted or cleaned up.`;
  }
  if (error instanceof ApiError && error.status === 400) {
    return `Run ${runId.slice(0, 10)} is not a valid run id.`;
  }
  const reason = unreadableReason(error);
  if (reason !== null) {
    return `Run ${runId.slice(0, 10)} cannot be read: ${reason}`;
  }
  return String(error);
}

/** Banner text for a ref whose history failed to load. A 422 is a torn reflog,
 *  and every view built on that history (for `baseline`, the mainline) is
 *  missing for that reason, so the message says so with the server's detail.
 *  Anything else keeps its detail. */
export function formatReflogLoadMessage(
  error: unknown,
  refName: string,
): string {
  const reason = unreadableReason(error);
  if (reason !== null) {
    return `The history of ${refName} cannot be read: ${reason}`;
  }
  return String(error);
}
