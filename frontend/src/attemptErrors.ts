import type { AttemptErrorRecord } from "./types";

/** One line naming what raised, for a cell or tooltip with room for one line.
 *
 *  A task that raised leads, because it is usually why an evaluator had nothing
 *  to judge; naming the evaluator would send the reader after a judge that was
 *  never the problem. The rest are counted, not listed. The case pane shows all
 *  of them.
 */
export function summariseErrors(
  errors: AttemptErrorRecord[] | undefined,
): string | null {
  if (!errors || errors.length === 0) return null;
  const task = errors.find((e) => !e.evaluator);
  if (task) return task.message;
  const [first, ...rest] = errors;
  if (!first) return null;
  const suffix = rest.length > 0 ? ` (+${rest.length} more)` : "";
  return `${first.evaluator}: ${first.message}${suffix}`;
}
