import type { Ref, RepositoryRole } from "./types";
import type { Selection } from "./components/Sidebar";

/** The slice of a mounted repository that baseline resolution reads. */
export interface BaselineSource {
  slug: string;
  role: RepositoryRole;
  baseline: Ref | null;
}

/** The mainline run to diff against, as a selection, or null when no remote
 *  is mounted or its baseline has no tip with a readable run.
 *
 *  The mainline lives on the remote and nowhere else. A `baseline` in the
 *  local repository is not it, with or without a remote beside it. */
export function resolveBaseline(
  repositories: readonly BaselineSource[],
): Selection | null {
  const mainlineRepository = repositories.find((r) => r.role === "remote");
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

/** How a repository's refs are listed. Only the remote has a mainline, so a
 *  `baseline` anywhere else is listed as an ordinary ref, first among them. */
export function splitMainline(repo: {
  role: RepositoryRole;
  baseline: Ref | null;
  otherRefs: Ref[];
}): { mainline: Ref | null; otherRefs: Ref[] } {
  if (repo.role === "remote" || repo.baseline === null) {
    return { mainline: repo.baseline, otherRefs: repo.otherRefs };
  }
  return { mainline: null, otherRefs: [repo.baseline, ...repo.otherRefs] };
}
