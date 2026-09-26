// Opening a pane whose values the run view left out. The pane shows a loading
// message while the whole cases are fetched. A case is fetched once and kept
// while its run is open. A fetch only shows its pane if nothing opened or
// closed the drawer in the meantime. Fetched cases are kept here, not written
// into the runs, so both sides of a diff keep the same envelopes and compare
// by the same hashes.

import type { DrawerContent } from "./components/Drawer";
import {
  caseKeyOf,
  collectDeferred,
  deferredBytes,
  resolveDeferred,
  type DeferredEnvelope,
} from "./deferred";
import type { CaseRecord } from "./types";

/** Fetches the whole case that an envelope belongs to. */
export type FetchCase = (envelope: DeferredEnvelope) => Promise<CaseRecord>;

export interface DrawerOpenerDeps {
  /** Shows a pane, or closes the drawer on null. */
  show: (content: DrawerContent | null) => void;
  onError: (message: string) => void;
}

export interface DrawerOpener {
  /** Shows `content`, after fetching the cases of its deferred values. A case
   *  fetched before is not fetched again. */
  open: (content: DrawerContent, fetchCase: FetchCase) => Promise<void>;
  close: () => void;
  /** Forgets the fetched cases of every run not in `runIds`. */
  keepRuns: (runIds: ReadonlySet<string>) => void;
}

export function createDrawerOpener({
  show,
  onError,
}: DrawerOpenerDeps): DrawerOpener {
  const cases = new Map<string, { run: string; record: CaseRecord }>();
  // Every open and close adds one.
  let changes = 0;

  const resolve = (content: DrawerContent): DrawerContent =>
    resolveDeferred(
      content,
      new Map([...cases].map(([key, { record }]) => [key, record])),
    );

  const open = async (
    content: DrawerContent,
    fetchCase: FetchCase,
  ): Promise<void> => {
    const ticket = ++changes;
    const deferred = collectDeferred(content);
    const missing = new Map<string, DeferredEnvelope>();
    for (const env of deferred) {
      const key = caseKeyOf(env);
      if (!cases.has(key)) missing.set(key, env);
    }
    if (missing.size === 0) {
      show(resolve(content));
      return;
    }
    show({
      kind: "loading",
      title: content.title,
      size: deferredBytes(deferred),
    });
    try {
      const fetched = await Promise.all(
        [...missing].map(async ([key, env]) => {
          return [key, env.run, await fetchCase(env)] as const;
        }),
      );
      for (const [key, run, record] of fetched) cases.set(key, { run, record });
    } catch (e: unknown) {
      if (ticket !== changes) return;
      show(null);
      onError(
        `The values of this case did not load: ${e instanceof Error ? e.message : String(e)}`,
      );
      return;
    }
    if (ticket === changes) show(resolve(content));
  };

  const close = (): void => {
    changes += 1;
    show(null);
  };

  const keepRuns = (runIds: ReadonlySet<string>): void => {
    for (const [key, { run }] of cases) {
      if (!runIds.has(run)) cases.delete(key);
    }
  };

  return { open, close, keepRuns };
}
