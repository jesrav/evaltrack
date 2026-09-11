// The stored form of a binary payload. A run records bytes it cannot store as
// UTF-8 text as `{"$binary": {"sha256": ..., "size": ...}}`. A fingerprint
// supports display and diffing by hash equality, where raw content supports
// neither. This module is the one place the dashboard gives that shape meaning.

import { formatBytes } from "./format";

export interface BinaryEnvelope {
  sha256: string;
  size: number;
}

/** The fingerprint inside `value`, or null for anything else.
 *
 *  Strict on the outer shape. Only an object whose single key is `$binary`
 *  counts, so user data that carries a `$binary` field among others never
 *  renders as binary. The inner object can grow extra fields without breaking
 *  detection. */
export function readBinaryEnvelope(value: unknown): BinaryEnvelope | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const keys = Object.keys(value);
  if (keys.length !== 1 || keys[0] !== "$binary") return null;
  const inner = (value as Record<string, unknown>)["$binary"];
  if (typeof inner !== "object" || inner === null || Array.isArray(inner)) {
    return null;
  }
  const { sha256, size } = inner as Record<string, unknown>;
  if (typeof sha256 !== "string" || typeof size !== "number") return null;
  return { sha256, size };
}

// Long enough to tell a handful of payloads apart at a glance. The full hash
// stays in the raw value for anything stronger.
const HASH_PREFIX = 4;

/** Chip label for a fingerprint, for example "binary · 1.2 MB · a3f9…". */
export function formatBinaryLabel(env: BinaryEnvelope): string {
  return `binary · ${formatBytes(env.size)} · ${env.sha256.slice(0, HASH_PREFIX)}…`;
}
