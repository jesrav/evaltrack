// The server replaces each large value in a run with an envelope,
// `{"$deferred": {...}}`. The envelope holds a preview for a cell, the size,
// and a hash to compare runs without the value. It also holds the address of
// the whole value, which a pane fetches when it opens. This module is the one
// place that reads the envelope.

import type { CaseRecord } from "./types";

/** Where in its run a deferred value lives. `attempt` is set for an attempt's
 *  output and absent for a case's own fields. */
export interface DeferredAddress {
  run: string;
  test: string;
  case: string;
  field: "inputs" | "expected_output" | "metadata" | "output";
  attempt?: number;
}

export interface DeferredEnvelope extends DeferredAddress {
  preview: string;
  size: number;
  sha256: string;
}

const FIELDS = new Set(["inputs", "expected_output", "metadata", "output"]);

/** The envelope inside `value`, or null for anything else. Only an object
 *  whose single key is `$deferred` counts, so user data that has this key
 *  among others is not read as an envelope. */
export function readDeferredEnvelope(value: unknown): DeferredEnvelope | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const keys = Object.keys(value);
  if (keys.length !== 1 || keys[0] !== "$deferred") return null;
  const inner = (value as Record<string, unknown>)["$deferred"];
  if (typeof inner !== "object" || inner === null || Array.isArray(inner)) {
    return null;
  }
  const env = inner as Record<string, unknown>;
  const { preview, size, sha256, run, test, field, attempt } = env;
  const caseId = env["case"];
  if (
    typeof preview !== "string" ||
    typeof size !== "number" ||
    typeof sha256 !== "string" ||
    typeof run !== "string" ||
    typeof test !== "string" ||
    typeof caseId !== "string" ||
    typeof field !== "string" ||
    !FIELDS.has(field)
  ) {
    return null;
  }
  const out: DeferredEnvelope = {
    preview,
    size,
    sha256,
    run,
    test,
    case: caseId,
    field: field as DeferredAddress["field"],
  };
  if (typeof attempt === "number") out.attempt = attempt;
  return out;
}

/** The key of a case. All deferred values of one case share it, so one fetch
 *  serves them all. */
export function caseKeyOf(address: DeferredAddress): string {
  return `${address.run}\u0000${address.test}\u0000${address.case}`;
}

/** Every deferred envelope inside `value`, walking plain data only. Two
 *  envelopes for one case count once, since one fetch serves both. */
export function collectDeferred(value: unknown): DeferredEnvelope[] {
  const found = new Map<string, DeferredEnvelope>();
  const seen = new Set<object>();
  const walk = (v: unknown): void => {
    if (typeof v !== "object" || v === null) return;
    if (seen.has(v)) return;
    seen.add(v);
    const env = readDeferredEnvelope(v);
    if (env) {
      found.set(
        `${caseKeyOf(env)}\u0000${env.field}\u0000${env.attempt ?? ""}`,
        env,
      );
      return;
    }
    for (const child of Array.isArray(v) ? v : Object.values(v)) walk(child);
  };
  walk(value);
  return [...found.values()];
}

/** The value an envelope stands for, read out of its fetched case. */
export function pickDeferredValue(
  address: DeferredAddress,
  record: CaseRecord,
): unknown {
  if (address.field === "output") {
    return record.attempts[address.attempt ?? 0]?.output;
  }
  return record[address.field];
}

/** `value` with every envelope replaced by the value it stands for, taken from
 *  `cases`, keyed by `caseKeyOf`. An envelope whose case is not there stays as
 *  it is. Returns the same reference when nothing was replaced. */
export function resolveDeferred<T>(
  value: T,
  cases: ReadonlyMap<string, CaseRecord>,
): T {
  const walk = (v: unknown): unknown => {
    if (typeof v !== "object" || v === null) return v;
    const env = readDeferredEnvelope(v);
    if (env) {
      const record = cases.get(caseKeyOf(env));
      return record ? pickDeferredValue(env, record) : v;
    }
    if (Array.isArray(v)) {
      const items = v.map(walk);
      return items.every((item, i) => item === v[i]) ? v : items;
    }
    const obj = v as Record<string, unknown>;
    let changed = false;
    const out: Record<string, unknown> = {};
    for (const [k, item] of Object.entries(obj)) {
      const next = walk(item);
      changed = changed || next !== item;
      out[k] = next;
    }
    return changed ? out : v;
  };
  return walk(value) as T;
}

/** The total bytes the envelopes stand for, for a loading message. */
export function deferredBytes(envelopes: DeferredEnvelope[]): number {
  return envelopes.reduce((sum, env) => sum + env.size, 0);
}
