// A tiny, dependency-free JSON renderer. Two public components share one set of
// primitives so the single-value viewer (JsonView) and the field-aware diff
// (JsonDiff, separate file) look and behave identically:
//
//   - leaf formatting (strings as text, numbers/booleans/null syntax-coloured),
//   - key styling,
//   - the collapse toggle for objects/arrays.
//
// Keys and structure use muted and accent tones via CSS vars. Nothing here
// touches --pass/--fail (green/red), which are reserved for verdicts.

import { useState } from "react";
import {
  readBinaryEnvelope,
  formatBinaryLabel,
  type BinaryEnvelope,
} from "../binaryEnvelope";
import { formatFallbackString, getPrimaryView } from "../extract";
import { CopyButton } from "./CopyButton";

// ---- shared primitives ---------------------------------------------------

/** True for values rendered as an expandable block (objects, arrays). Anything
 *  else is a leaf rendered inline. `null` is a leaf (renders as a muted dash). */
export function isContainer(value: unknown): value is object {
  return typeof value === "object" && value !== null && value !== undefined;
}

/** True when a diff of the two values can say something.
 *
 *  Two containers get a per-key diff, and two leaves get a line diff. A
 *  container against a leaf has nothing in common to line up, so the diff
 *  degrades to "all of this became all of that", which the two values shown
 *  side by side already say, and say better. */
export function diffCanCompare(a: unknown, b: unknown): boolean {
  return isContainer(getPrimaryView(a)) === isContainer(getPrimaryView(b));
}

/** Entries of a container as [key, value] pairs. Arrays key by index so the
 *  recursive renderer can treat objects and arrays uniformly. */
export function containerEntries(value: object): [string, unknown][] {
  if (Array.isArray(value)) {
    return value.map((v, i) => [String(i), v]);
  }
  return Object.entries(value as Record<string, unknown>);
}

/** A node is "big" (collapsed by default) when it carries many entries or nests
 *  very deeply. Nested objects expand by default so structures like `source`
 *  look like ordinary JSON. Only genuinely large or deep nodes collapse. */
export function shouldCollapse(value: object, depth: number): boolean {
  return containerEntries(value).length > 12 || depth >= 6;
}

/** True for a string that renders as nothing, empty or only whitespace. Such a
 *  value needs quoting to be visible at all. */
export function rendersBlank(value: string): boolean {
  return value.trim() === "";
}

/** Render a scalar leaf with type-appropriate styling. Strings render as plain
 *  text rather than quoted JSON. Null and undefined become a muted em dash.
 *  Exported so the diff renders the same leaves on both sides of a change. */
export function JsonLeaf({ value }: { value: unknown }) {
  // Render JSON `null` literally (it's real data). Only genuinely-absent
  // (undefined) collapses to a muted dash.
  if (value === null) {
    return <span className="json-null">null</span>;
  }
  if (value === undefined) {
    return <span className="json-null">—</span>;
  }
  switch (typeof value) {
    case "string":
      // Plain text, not "quoted", since the common case is human-readable output.
      // A blank one is the exception. Rendered bare it looks like a missing
      // value, so it gets quotes and the muted styling of an empty value.
      if (rendersBlank(value)) {
        return <span className="json-null">{`"${value}"`}</span>;
      }
      return <span className="json-string">{value}</span>;
    case "number":
      return <span className="json-number">{String(value)}</span>;
    case "boolean":
      return <span className="json-boolean">{value ? "true" : "false"}</span>;
    default:
      return <span className="json-string">{formatFallbackString(value)}</span>;
  }
}

/** A key label in an object row (or an index in an array). */
export function JsonKey({ name }: { name: string }) {
  return <span className="json-key">{name}</span>;
}

/** A recorded binary payload. The run stores a sha256+size fingerprint, not
 *  the content, so a chip naming what is known beats rendering the envelope
 *  as an object. Hover carries the full hash. */
export function BinaryChip({ env }: { env: BinaryEnvelope }) {
  return (
    <span className="json-binary" title={`sha256 ${env.sha256}`}>
      {formatBinaryLabel(env)}
    </span>
  );
}

/** Collapse/expand control plus a one-line summary of what is hidden, e.g.
 *  "{…} 4 keys" / "[…] 7 items". */
export function CollapseToggle({
  open,
  value,
  onToggle,
}: {
  open: boolean;
  value: object;
  onToggle: () => void;
}) {
  const isArr = Array.isArray(value);
  const n = containerEntries(value).length;
  const noun = isArr ? (n === 1 ? "item" : "items") : n === 1 ? "key" : "keys";
  const braces = isArr ? "[…]" : "{…}";
  return (
    <button type="button" className="json-toggle" onClick={onToggle}>
      <span className="json-caret">{open ? "▾" : "▸"}</span>
      {!open && (
        <span className="json-collapsed">
          {braces} {n} {noun}
        </span>
      )}
    </button>
  );
}

// ---- JsonView ------------------------------------------------------------

/** Recursive, collapsible, syntax-coloured JSON view. Leaves render inline.
 *  Objects and arrays render as indented, collapsible blocks.
 *
 *  The copy button carries the whole value. Selecting the rendered tree gives
 *  `key: value` lines, which is not valid JSON. An absent value has nothing to
 *  copy. */
export function JsonView({ value }: { value: unknown }) {
  return (
    <div className="json-view">
      {value !== undefined && (
        <div className="json-view-actions">
          <CopyButton value={value} />
        </div>
      )}
      <JsonNode value={value} depth={0} />
    </div>
  );
}

function JsonNode({ value, depth }: { value: unknown; depth: number }) {
  const env = readBinaryEnvelope(value);
  if (env) {
    return <BinaryChip env={env} />;
  }
  if (!isContainer(value)) {
    return <JsonLeaf value={value} />;
  }
  return <JsonContainer value={value} depth={depth} />;
}

function JsonContainer({ value, depth }: { value: object; depth: number }) {
  const [open, setOpen] = useState(!shouldCollapse(value, depth));
  const entries = containerEntries(value);
  const isArr = Array.isArray(value);

  if (entries.length === 0) {
    return <span className="json-collapsed">{isArr ? "[]" : "{}"}</span>;
  }

  // When expanded, the caret and opening brace sit on the parent line, the
  // children are indented, and a closing brace ends on its own line, so it
  // looks like ordinary JSON. When collapsed, the toggle shows the `{…} N keys`
  // summary.
  return (
    <>
      <CollapseToggle
        open={open}
        value={value}
        onToggle={() => setOpen((o) => !o)}
      />
      {open && (
        <>
          <span className="json-bracket">{isArr ? "[" : "{"}</span>
          <div className="json-children">
            {entries.map(([k, v]) => (
              <div className="json-row" key={k}>
                <JsonKey name={k} />
                <span className="json-colon">:</span>{" "}
                <JsonNode value={v} depth={depth + 1} />
              </div>
            ))}
          </div>
          <span className="json-bracket json-bracket-close">
            {isArr ? "]" : "}"}
          </span>
        </>
      )}
    </>
  );
}
