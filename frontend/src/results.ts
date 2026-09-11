import type { EvaluatorResult, ResultKind } from "./types";

/** Which of the two shapes a result is. Read off the value, because the run
 *  stores no field for it: the value already says. */
export function kindOf(result: EvaluatorResult): ResultKind {
  return typeof result.value === "boolean" ? "assertion" : "score";
}

/** The results of one kind, keyed as they were.
 *
 *  An attempt carries every evaluator answer in one map, because what gates a
 *  case is the verdict rather than the shape of the value. The dashboard still
 *  groups by shape (a number shows as a meter, an assertion as a pass mark),
 *  and this is that grouping, nothing more. */
export function resultsOfKind(
  results: Record<string, EvaluatorResult> | undefined,
  kind: ResultKind,
): Record<string, EvaluatorResult> {
  const picked: Record<string, EvaluatorResult> = {};
  for (const [name, result] of Object.entries(results ?? {})) {
    if (kindOf(result) === kind) picked[name] = result;
  }
  return picked;
}

/** Whether the attempt produced any result of this kind. */
export function hasKind(
  results: Record<string, EvaluatorResult> | undefined,
  kind: ResultKind,
): boolean {
  return Object.values(results ?? {}).some((r) => kindOf(r) === kind);
}

/** A result's value as a number, or null when it is not one.
 *
 *  A boolean is deliberately not a number. `true >= 0.5` holds in JavaScript,
 *  so a bar silently grades an assertion. */
export function numericValue(result: EvaluatorResult): number | null {
  return typeof result.value === "number" ? result.value : null;
}
