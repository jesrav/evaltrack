import type { ReactNode } from "react";
import { useCallback, useLayoutEffect, useRef, useState } from "react";

import { isClamped } from "../clamp";

/** Horizontal scroll container for a wide case table, with a fade at whichever
 *  edge has content beyond it.
 *
 *  Whether a table overflows depends on the window and on the data, so this
 *  measures the overflow instead of assuming it. A fade with nothing behind it
 *  misleads as much as no fade at all. */
export function TableScroll({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const hidden = el.scrollWidth - el.clientWidth;
    // A fractional scrollLeft at the far right otherwise leaves the end fade on
    // with nothing behind it.
    const left = Math.round(el.scrollLeft);
    setEdges({
      start: left > 1,
      end: isClamped(el.scrollWidth, el.clientWidth) && left < hidden - 1,
    });
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // The table's own width changes with the data (a drawer opening, an attempt
    // switching), not only with the container's.
    const table = el.firstElementChild;
    if (table) ro.observe(table);
    return () => ro.disconnect();
  }, [measure]);

  const cls =
    "table-frame" +
    (edges.start ? " fade-start" : "") +
    (edges.end ? " fade-end" : "");
  return (
    <div className={cls}>
      <div className="table-scroll" ref={ref} onScroll={measure}>
        {children}
      </div>
    </div>
  );
}
