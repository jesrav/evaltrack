import type { Ref, RepositoryRole } from "./types";
import type { Selection } from "./components/Sidebar";

/** The slice of a mounted repository that baseline resolution reads. */
export interface BaselineSource {
  slug: string;
  role: RepositoryRole;
  baseline: Ref | null;
}

/** The mainline run to diff against, as a selection, or null when the
 *  mainline repository has no baseline tip with a readable run.
 *
 *  The mainline lives on the remote. Without one, the sole repository is it.
 *  The server picks the mainline for reliability and score history by the same
 *  rule, so a local-only setup that promotes with `--local` gets the compare
 *  button as well as the trend panels, and a local baseline beside a remote is
 *  nobody's mainline. */
export function resolveBaseline(
  repositories: readonly BaselineSource[],
): Selection | null {
  const mainlineRepository =
    repositories.find((r) => r.role === "remote") ??
    (repositories.length === 1 ? repositories[0] : undefined);
  const baseline = mainlineRepository?.baseline;
  // The server nulls `tip_run` when the tip's run cannot be read, so the tip
  // alone does not prove there is a run to compare against.
  if (!mainlineRepository || !baseline?.tip || !baseline.tip_run) return null;
  return {
    repository: mainlineRepository.slug,
    runId: baseline.tip.run_id,
    via: baseline.name,
  };
}
