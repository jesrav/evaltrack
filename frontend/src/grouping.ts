// Group recorded tests by their pytest module path.

import type { RunRecord, RecordedTest } from "./types";

export const UNKNOWN_MODULE = "(unknown module)";

export interface ModuleGroup {
  module: string;
  // Tests in this module that recorded an eval, sorted by name.
  tests: string[];
  // Tests in this module that never evaluated. They were skipped, often for
  // a missing API key, or they failed or errored before `evaluate()`. A run only
  // records evaltrack-marked tests, so each one is an eval someone asked for.
  // Dropping them puts the module counts below the run-level ones.
  unevaluatedTests: string[];
}

export function groupTestsByModule(run: RunRecord): ModuleGroup[] {
  const evaluated = new Map<string, string[]>();
  const unevaluated = new Map<string, string[]>();
  for (const [nodeid, test] of Object.entries(run.tests)) {
    const bucket = test.marker ? evaluated : unevaluated;
    push(bucket, test.test_file ?? UNKNOWN_MODULE, nodeid);
  }
  return sortModules([...evaluated.keys(), ...unevaluated.keys()]).map(
    (module) => ({
      module,
      tests: (evaluated.get(module) ?? []).sort((a, b) => a.localeCompare(b)),
      unevaluatedTests: (unevaluated.get(module) ?? []).sort((a, b) =>
        a.localeCompare(b),
      ),
    }),
  );
}

export function groupNodeidsByModule(
  nodeids: string[],
  tests: Record<string, RecordedTest> | undefined,
): ModuleGroup[] {
  const byModule = new Map<string, string[]>();
  for (const name of nodeids) {
    push(byModule, tests?.[name]?.test_file ?? UNKNOWN_MODULE, name);
  }
  return sortModules([...byModule.keys()]).map((module) => ({
    module,
    tests: (byModule.get(module) ?? []).sort((a, b) => a.localeCompare(b)),
    unevaluatedTests: [],
  }));
}

function push<T>(map: Map<string, T[]>, key: string, value: T): void {
  const bucket = map.get(key);
  if (bucket) bucket.push(value);
  else map.set(key, [value]);
}

function sortModules(modules: string[]): string[] {
  return [...new Set(modules)].sort((a, b) => {
    if (a === UNKNOWN_MODULE) return 1;
    if (b === UNKNOWN_MODULE) return -1;
    return a.localeCompare(b);
  });
}
