// Heuristics for picking out the meaningful part of an arbitrary value.
//
// Pydantic AI tasks return an `AgentRunResult` shaped like:
//
//   { output: "Hi", _output_tool_name: null, _state: { ...big... }, ... }
//
// where the actual answer lives at `.output` and every other top-level key is
// `_`-prefixed metadata (state, traceparent, ...). The cell shows `"Hi"`, not
// the 4 KB blob, and the diff treats "same answer, different timestamps" as
// unchanged.
//
// The drawer view always renders the original value. Nothing here is lossy.

import { readBinaryEnvelope, formatBinaryLabel } from "./binaryEnvelope";

const RESULT_KEYS = new Set(["output", "result", "answer", "content"]);
const MAX_UNWRAP_DEPTH = 4;

/** Return the "interesting" value inside a possibly-wrapped result.
 *
 * The rule is conservative. Unwrap only when the object has exactly one
 * non-`_` key and that key is a recognised result name. Recurses up to a few
 * levels so wrappers around wrappers also collapse. Anything unrecognised
 * (structured outputs like `{answer, confidence}`, arrays, primitives) is
 * returned unchanged. */
export function getPrimaryView(value: unknown, depth = 0): unknown {
  if (depth >= MAX_UNWRAP_DEPTH) return value;
  if (value === null || value === undefined) return value;
  if (typeof value !== "object" || Array.isArray(value)) return value;
  const obj = value as Record<string, unknown>;
  const publicKeys = Object.keys(obj).filter((k) => !k.startsWith("_"));
  if (publicKeys.length === 1 && RESULT_KEYS.has(publicKeys[0]!)) {
    return getPrimaryView(obj[publicKeys[0]!], depth + 1);
  }
  return value;
}

/** Last-resort `String()` for arbitrary user payloads the structured renderers
 *  cannot handle, such as primitives behind an un-narrowed type or objects
 *  whose `JSON.stringify` throws (circular refs, BigInt). This is the one
 *  deliberate exception to `no-base-to-string`. An occasional "[object Object]"
 *  beats throwing mid-render. */
export function formatFallbackString(value: unknown): string {
  return String(value);
}

/** Stable string rendering used by canonical equality (diff comparison). */
export function formatCanonicalText(value: unknown): string {
  const primary = getPrimaryView(value);
  if (primary === null || primary === undefined) return String(primary);
  if (typeof primary === "string") return primary;
  if (typeof primary !== "object") return formatFallbackString(primary);
  try {
    return JSON.stringify(primary, sortKeys);
  } catch {
    return formatFallbackString(primary);
  }
}

/** Human-friendly rendering used by the inline cells and drawer's "primary"
 *  tab. Strings are returned as-is, anything else as pretty-printed JSON. A
 *  binary fingerprint renders as its label, because the envelope carries no
 *  content and its JSON says less than "binary · size · hash". */
export function formatDisplayText(value: unknown, pretty = false): string {
  const primary = getPrimaryView(value);
  const env = readBinaryEnvelope(primary);
  if (env) return formatBinaryLabel(env);
  if (primary === null || primary === undefined) return "—";
  if (typeof primary === "string") return primary;
  if (typeof primary !== "object") return formatFallbackString(primary);
  try {
    return pretty ? JSON.stringify(primary, null, 2) : JSON.stringify(primary);
  } catch {
    return formatFallbackString(primary);
  }
}

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}

function sortKeys(_key: string, value: unknown): unknown {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return value;
  }
  const obj = value as Record<string, unknown>;
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(obj).sort()) sorted[k] = obj[k];
  return sorted;
}
