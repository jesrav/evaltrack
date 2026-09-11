import { useMemo } from "react";
import { diffLines, type Change } from "diff";

interface Props {
  a: string;
  b: string;
  labelA: string;
  labelB: string;
  // Show the BASE/COMPARE legend above the diff. Off when the surrounding view
  // already labels the two sides (e.g. a long-string leaf inside JsonDiff).
  showLegend?: boolean;
}

/** Edit-distance budget for one pair of outputs, in changed lines. jsdiff runs
 *  in O(N*D), and an agent transcript that a run rewrote end to end is thousands
 *  of lines of pure D. Uncapped, it takes minutes on the main thread and the tab
 *  never comes back. Measured with the installed jsdiff version, aborting at
 *  1200 takes about 0.1s whatever the inputs, while a 12 000-line transcript
 *  with up to 600 changed lines still diffs in full. */
const MAX_EDIT_LENGTH = 1200;

/** The line diff of two outputs, or null when they are too far apart to diff
 *  inside the budget and both sides are shown in full instead. */
export function lineDiff(a: string, b: string): Change[] | null {
  return diffLines(a, b, { maxEditLength: MAX_EDIT_LENGTH }) ?? null;
}

// Line-by-line text diff using jsdiff. Removed (BASE) lines are blue, added
// (COMPARE) lines amber, unchanged lines muted gray. Green and red stay
// reserved for verdicts. A single column rather than a split one, so long lines
// do not force horizontal scrolling.
export function TextDiff({ a, b, labelA, labelB, showLegend = true }: Props) {
  const parts = useMemo(() => lineDiff(a, b), [a, b]);
  if (parts === null)
    return <BothSides a={a} b={b} labelA={labelA} labelB={labelB} />;
  return (
    <div className="text-diff">
      {showLegend && (
        <div className="text-diff-legend">
          <span className="legend-pill removed">− {labelA}</span>
          <span className="legend-pill added">+ {labelB}</span>
        </div>
      )}
      <pre className="text-diff-body">
        {parts.map((p, i) => {
          const cls = p.added
            ? "diff-line added"
            : p.removed
              ? "diff-line removed"
              : "diff-line context";
          const prefix = p.added ? "+ " : p.removed ? "- " : "  ";
          // jsdiff returns each chunk with its trailing \n. Split so every
          // visible line gets a prefix.
          const lines = p.value.replace(/\n$/, "").split("\n");
          return (
            <span key={i} className={cls}>
              {lines.map((line, j) => (
                <span key={j} className="diff-line-row">
                  {prefix}
                  {line || " "}
                </span>
              ))}
            </span>
          );
        })}
      </pre>
    </div>
  );
}

// The fallback when the pair blew the budget. Both sides in full, always
// labelled, since stacked untitled blobs say nothing about which run is which.
// Each side is one text node rather than a span per line, because the pair that
// defeats the diff is also the pair whose line-by-line render is slow.
function BothSides({ a, b, labelA, labelB }: Omit<Props, "showLegend">) {
  return (
    <div className="text-diff">
      <p className="text-diff-note">
        Too many changed lines to diff. Showing both sides in full.
      </p>
      <div className="text-diff-legend">
        <span className="legend-pill removed">− {labelA}</span>
      </div>
      <pre className="text-diff-body">{a}</pre>
      <div className="text-diff-legend">
        <span className="legend-pill added">+ {labelB}</span>
      </div>
      <pre className="text-diff-body">{b}</pre>
    </div>
  );
}
