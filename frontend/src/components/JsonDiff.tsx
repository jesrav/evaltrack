// Field-aware recursive diff of two values (A = BASE, B = COMPARE). Shares the
// leaf/key/collapse primitives with JsonView, adding per-key classification
// (changed / added / removed / unchanged) and an inline TextDiff for
// long/multiline string leaves. BASE is blue and COMPARE amber. Green and red
// stay reserved for verdicts.

import { createContext, useContext, useMemo, useState } from "react";
import { readBinaryEnvelope, formatBinaryLabel } from "../binaryEnvelope";
import { formatFallbackString } from "../extract";
import { formatScore } from "../format";
import { TextDiff } from "./TextDiff";
import {
  BinaryChip,
  CollapseToggle,
  JsonKey,
  JsonLeaf,
  containerEntries,
  isContainer,
  shouldCollapse,
} from "./JsonView";

// A leaf string is "long" when it spans multiple lines or is wide enough that a
// word/line diff beats a "x → y" scalar render (e.g. an evaluator reason).
const LONG_STRING = 60;

function isLongString(v: unknown): v is string {
  return typeof v === "string" && (v.length > LONG_STRING || v.includes("\n"));
}

/** Stable, order-insensitive equality used to classify keys as unchanged. */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (isContainer(a) && isContainer(b)) {
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const ea = containerEntries(a);
    const eb = containerEntries(b);
    if (ea.length !== eb.length) return false;
    const mb = new Map(eb);
    for (const [k, va] of ea) {
      if (!mb.has(k)) return false;
      if (!deepEqual(va, mb.get(k))) return false;
    }
    return true;
  }
  return false;
}

// ---- one-line change summary (for the diff-cell teaser) -------------------

interface LeafChange {
  path: string;
  kind: "changed" | "added" | "removed";
  a?: unknown;
  b?: unknown;
}

/** Walk two values and collect the leaf-level changes with dotted paths, so a
 *  changed object can be summarized by *what* changed rather than a char count. */
function collectLeafChanges(
  a: unknown,
  b: unknown,
  path: string,
  out: LeafChange[],
): void {
  if (
    isContainer(a) &&
    isContainer(b) &&
    Array.isArray(a) === Array.isArray(b) &&
    // A binary fingerprint is one value, so "the payload changed" says more
    // than a per-field walk into its hash and size.
    !readBinaryEnvelope(a) &&
    !readBinaryEnvelope(b)
  ) {
    const ma = new Map(containerEntries(a));
    const mb = new Map(containerEntries(b));
    const keys: string[] = [...ma.keys()];
    for (const k of mb.keys()) if (!ma.has(k)) keys.push(k);
    for (const k of keys) {
      const childPath = path ? `${path}.${k}` : k;
      const inA = ma.has(k);
      const inB = mb.has(k);
      if (inA && inB) {
        if (!deepEqual(ma.get(k), mb.get(k))) {
          collectLeafChanges(ma.get(k), mb.get(k), childPath, out);
        }
      } else if (inA) {
        out.push({ path: childPath, kind: "removed", a: ma.get(k) });
      } else {
        out.push({ path: childPath, kind: "added", b: mb.get(k) });
      }
    }
    return;
  }
  out.push({ path, kind: "changed", a, b });
}

function fmtScalar(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "—";
  const env = readBinaryEnvelope(v);
  if (env) return formatBinaryLabel(env);
  if (isContainer(v)) return Array.isArray(v) ? "[…]" : "{…}";
  if (typeof v === "string") return v.length > 24 ? `${v.slice(0, 24)}…` : v;
  if (typeof v === "number") return formatScore(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return formatFallbackString(v);
}

/** A concise label for a changed value, used in the diff-cell teaser. Names the
 *  field when exactly one leaf changed (e.g. "is_question: true → false"),
 *  otherwise counts them. The click-through shows the full field-aware diff. */
export function summarizeChange(a: unknown, b: unknown): string {
  const changes: LeafChange[] = [];
  collectLeafChanges(a, b, "", changes);
  const [c] = changes;
  if (c === undefined) return "changed";
  if (changes.length === 1) {
    if (c.kind === "added") return `+ ${c.path}`;
    if (c.kind === "removed") return `− ${c.path}`;
    const lhs = c.path ? `${c.path}: ` : "";
    return `${lhs}${fmtScalar(c.a)} → ${fmtScalar(c.b)}`;
  }
  return `${changes.length} fields changed`;
}

type Presence = "both" | "a-only" | "b-only";

// What the two sides are called, for the markers deep in the tree. Two runs is
// the usual pair, but not the only one, so the names are props.
const SideLabels = createContext({ a: "BASE", b: "COMPARE" });

/** Field-aware diff. Two container roots diff per key. Anything else falls back
 *  to a scalar or string diff of the two roots. */
export function JsonDiff({
  a,
  b,
  labelA = "BASE",
  labelB = "COMPARE",
}: {
  a: unknown;
  b: unknown;
  labelA?: string;
  labelB?: string;
}) {
  const labels = useMemo(() => ({ a: labelA, b: labelB }), [labelA, labelB]);
  return (
    <SideLabels.Provider value={labels}>
      <div className="json-view json-diff">
        <DiffNode a={a} b={b} presence="both" depth={0} />
      </div>
    </SideLabels.Provider>
  );
}

function DiffNode({
  a,
  b,
  presence,
  depth,
}: {
  a: unknown;
  b: unknown;
  presence: Presence;
  depth: number;
}) {
  // A binary fingerprint diffs as one value, never per key. Equal hashes are
  // the same payload, different hashes a changed one.
  if (readBinaryEnvelope(a) || readBinaryEnvelope(b)) {
    return <DiffLeaf a={a} b={b} presence={presence} />;
  }
  if (
    presence === "both" &&
    isContainer(a) &&
    isContainer(b) &&
    Array.isArray(a) === Array.isArray(b)
  ) {
    return <DiffContainer a={a} b={b} depth={depth} />;
  }
  // A side is missing, or the two roots are scalars/strings, or their shapes
  // diverged (object vs scalar). Render as a leaf-level diff.
  return <DiffLeaf a={a} b={b} presence={presence} />;
}

function DiffContainer({
  a,
  b,
  depth,
}: {
  a: object;
  b: object;
  depth: number;
}) {
  // Default-collapse on the same heuristic as JsonView, but only when nothing
  // inside changed. A node with a real change must be open so it is visible.
  const changed = !deepEqual(a, b);
  const [open, setOpen] = useState(
    changed || !shouldCollapse(a, depth) || !shouldCollapse(b, depth),
  );

  const ma = new Map(containerEntries(a));
  const mb = new Map(containerEntries(b));
  const keys: string[] = [...ma.keys()];
  for (const k of mb.keys()) if (!ma.has(k)) keys.push(k);
  const isArr = Array.isArray(a);

  return (
    <>
      <CollapseToggle
        open={open}
        value={a}
        onToggle={() => setOpen((o) => !o)}
      />
      {open && (
        <>
          <span className="json-bracket">{isArr ? "[" : "{"}</span>
          <div className="json-children">
            {keys.map((k) => {
              const inA = ma.has(k);
              const inB = mb.has(k);
              const va = ma.get(k);
              const vb = mb.get(k);
              const presence: Presence = !inB
                ? "a-only"
                : !inA
                  ? "b-only"
                  : "both";
              const unchanged = presence === "both" && deepEqual(va, vb);
              return (
                <div
                  className={`json-row diff-row ${rowClass(
                    presence,
                    unchanged,
                  )}`}
                  key={k}
                >
                  <DiffMarker presence={presence} unchanged={unchanged} />
                  <JsonKey name={k} />
                  <span className="json-colon">:</span>{" "}
                  <DiffNode
                    a={va}
                    b={vb}
                    presence={presence}
                    depth={depth + 1}
                  />
                </div>
              );
            })}
          </div>
          <span className="json-bracket json-bracket-close">
            {isArr ? "]" : "}"}
          </span>
        </>
      )}
    </>
  );
}

// A leaf-level change. Long strings get an inline TextDiff. Everything else
// renders "BASE → COMPARE" inline. Added and removed render the single side
// present.
function DiffLeaf({
  a,
  b,
  presence,
}: {
  a: unknown;
  b: unknown;
  presence: Presence;
}) {
  const labels = useContext(SideLabels);
  if (presence === "a-only") return <SideValue value={a} side="base" />;
  if (presence === "b-only") return <SideValue value={b} side="compare" />;

  if (deepEqual(a, b)) {
    // An unchanged leaf renders once, dimmed by the row class above.
    return <ValueRender value={a} />;
  }

  // A long or multiline string field (e.g. a reason) gets an inline text diff.
  if (isLongString(a) || isLongString(b)) {
    return (
      <div className="json-leaf-textdiff">
        <TextDiff
          a={typeof a === "string" ? a : formatFallbackString(a ?? "")}
          b={typeof b === "string" ? b : formatFallbackString(b ?? "")}
          labelA={labels.a}
          labelB={labels.b}
          showLegend={false}
        />
      </div>
    );
  }

  // A scalar change, or a shape divergence such as object → scalar, renders
  // BASE → COMPARE.
  return (
    <span className="json-scalar-change">
      <SideValue value={a} side="base" />
      <span className="diff-arrow"> → </span>
      <SideValue value={b} side="compare" />
    </span>
  );
}

// One side of a scalar change, tinted by role. Containers (shape divergence)
// fall back to a collapsed summary so structure is never silently dropped.
function SideValue({
  value,
  side,
}: {
  value: unknown;
  side: "base" | "compare";
}) {
  return (
    <span className={`json-side json-side-${side}`}>
      <ValueRender value={value} />
    </span>
  );
}

// Render a value that lives on one side of a leaf diff. Scalars use JsonLeaf.
// A container here means the two sides diverged in shape, so show its braces
// summary rather than recursing (the per-key diff only applies when both sides
// are the same kind of container).
function ValueRender({ value }: { value: unknown }) {
  const env = readBinaryEnvelope(value);
  if (env) {
    return <BinaryChip env={env} />;
  }
  if (isContainer(value)) {
    const n = containerEntries(value).length;
    const isArr = Array.isArray(value);
    return (
      <span className="json-collapsed">
        {isArr ? "[…]" : "{…}"} {n} {isArr ? "items" : "keys"}
      </span>
    );
  }
  return <JsonLeaf value={value} />;
}

// Small per-row marker so changed/added/removed are picked out without colour.
function DiffMarker({
  presence,
  unchanged,
}: {
  presence: Presence;
  unchanged: boolean;
}) {
  const labels = useContext(SideLabels);
  if (presence === "a-only")
    return (
      <span className="diff-marker" title={`only in ${labels.a}`}>
        −
      </span>
    );
  if (presence === "b-only")
    return (
      <span className="diff-marker" title={`only in ${labels.b}`}>
        +
      </span>
    );
  if (!unchanged)
    return (
      <span className="diff-marker" title="changed">
        ·
      </span>
    );
  return <span className="diff-marker diff-marker-blank"> </span>;
}

function rowClass(presence: Presence, unchanged: boolean): string {
  if (presence === "a-only" || presence === "b-only") return "diff-changed";
  return unchanged ? "diff-unchanged" : "diff-changed";
}
