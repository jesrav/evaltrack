// Thin fetch wrappers over the dashboard's HTTP API.

import type {
  CaseRecord,
  RunRecord,
  MainlineEntry,
  ProjectConfig,
  ReflogEntry,
  Ref,
  RefDeletion,
  RepositoryInfo,
  RunHistory,
  RunSummary,
} from "./types";

/** A failed HTTP response, carrying the status so callers can distinguish a
 *  genuine 404 ("not here") from a transient/server error worth surfacing. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Encode a path that can contain `/`-separated segments, escaping each segment
 *  but leaving the separators intact. The API serves namespaced ref names as
 *  multi-segment paths, so a name like `pr/123` keeps its slash while a
 *  stray `?`, `#`, or space in a name or slug is escaped rather than breaking
 *  the URL. Single-segment ids/slugs (no `/`) round-trip unchanged. */
function encodePath(value: string): string {
  return value.split("/").map(encodeURIComponent).join("/");
}

/** The server's `detail` from a failed response, when it sent one. A 409
 *  carries the refs that still reference a run, a 422 says why an object cannot
 *  be read, and the caller shows either. Null for a non-JSON body. */
async function readDetail(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.json();
    const d = (body as { detail?: unknown })?.detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d) return String(d.message);
  } catch {
    // A non-JSON body has no detail.
  }
  return null;
}

/** How much of a response body has arrived. `total` is the Content-Length,
 *  null when the server sent none. */
export interface Progress {
  loaded: number;
  total: number | null;
}

/** Parses the body as JSON, and calls `onProgress` for each chunk that
 *  arrives. The parse at the end reports no progress. */
export async function readJsonWithProgress<T>(
  res: Response,
  onProgress: (progress: Progress) => void,
): Promise<T> {
  const header = res.headers.get("content-length");
  const total = header ? Number(header) || null : null;
  if (!res.body) return (await res.json()) as T;
  let loaded = 0;
  const counted = res.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        loaded += chunk.byteLength;
        onProgress({ loaded, total });
        controller.enqueue(chunk);
      },
    }),
  );
  return (await new Response(counted).json()) as T;
}

async function getJson<T>(
  url: string,
  onProgress?: (progress: Progress) => void,
): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    // Without a detail, the URL is the only clue a server or proxy fault leaves.
    const detail = await readDetail(res);
    throw new ApiError(
      res.status,
      detail ?? `${res.status} ${res.statusText} for ${url}`,
    );
  }
  if (onProgress) return readJsonWithProgress<T>(res, onProgress);
  return (await res.json()) as T;
}

async function sendJson<T>(url: string, method: string): Promise<T> {
  // Browsers attach an Origin header to non-safe methods. The server rejects
  // any whose host is not the dashboard's own, so this DELETE only works
  // same-origin, which is the CSRF guard.
  const res = await fetch(url, { method });
  if (!res.ok) {
    // The request URL is left out on purpose. These messages go to the user,
    // and a repository slug is a long, unreadable path segment.
    const detail = await readDetail(res);
    throw new ApiError(res.status, detail ?? `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  config(): Promise<ProjectConfig> {
    return getJson("/api/config");
  },
  repositories(): Promise<RepositoryInfo[]> {
    return getJson("/api/repositories");
  },
  runs(slug: string, limit = 100, offset = 0): Promise<RunSummary[]> {
    return getJson(
      `/api/repositories/${encodePath(
        slug,
      )}/runs?limit=${limit}&offset=${offset}`,
    );
  },
  /** The run, with each large value replaced by a `$deferred` envelope (see
   *  `deferred.ts`). The bytes that arrive go to `onProgress`. */
  run(
    slug: string,
    id: string,
    onProgress?: (progress: Progress) => void,
  ): Promise<RunRecord> {
    return getJson(
      `/api/repositories/${encodePath(slug)}/runs/${encodePath(id)}`,
      onProgress,
    );
  },
  /** One case, whole. The test and the case go in query parameters, because a
   *  nodeid contains `::` and `/`. */
  runCase(
    slug: string,
    id: string,
    test: string,
    caseId: string,
  ): Promise<CaseRecord> {
    const q = new URLSearchParams({ test, case: caseId });
    return getJson(
      `/api/repositories/${encodePath(slug)}/runs/${encodePath(id)}/cases?${q}`,
    );
  },
  runMainline(slug: string, id: string): Promise<MainlineEntry | null> {
    return getJson(
      `/api/repositories/${encodePath(slug)}/runs/${encodePath(id)}/mainline`,
    );
  },
  /** URL of the whole stored run JSON as a file attachment.
   *  Used as an `<a download>` href. The server sets Content-Disposition. */
  runDownloadUrl(slug: string, id: string): string {
    return `/api/repositories/${encodePath(slug)}/runs/${encodePath(
      id,
    )}/download`;
  },
  /** URL of the run as the single-file HTML report, as a file attachment, to
   *  hand to someone without the dashboard. With `against`, the page opens on
   *  the comparison from that run to this one. The `via` labels are the refs
   *  the runs were reached by, so the page names them as this one does. */
  runReportUrl(
    slug: string,
    id: string,
    opts: {
      via?: string;
      against?: { slug: string; id: string; via?: string };
    } = {},
  ): string {
    const q = new URLSearchParams();
    if (opts.via) q.set("via", opts.via);
    if (opts.against) {
      q.set("against", opts.against.id);
      q.set("against_slug", opts.against.slug);
      if (opts.against.via) q.set("against_via", opts.against.via);
    }
    // Read the text rather than `size`, which browsers before 2023 lack.
    const text = q.toString();
    const query = text ? `?${text}` : "";
    return `/api/repositories/${encodePath(slug)}/runs/${encodePath(
      id,
    )}/report${query}`;
  },
  /** Pooled reliability and per-case score history over the mainline, with the
   *  viewed run as the newest point. One request, because the server measures
   *  both over the same history. */
  history(slug: string, runId?: string): Promise<RunHistory> {
    const q = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return getJson(`/api/repositories/${encodePath(slug)}/history${q}`);
  },
  /** Every ref with its namespace and its tip entry, so a listing needs no
   *  per-ref follow-up. */
  refs(slug: string): Promise<Ref[]> {
    return getJson(`/api/repositories/${encodePath(slug)}/refs`);
  },
  /** Reflog entries newest first. Offset 0 starts at the ref's current tip, and
   *  growing offsets page back in time. */
  refLog(
    slug: string,
    name: string,
    limit = 100,
    offset = 0,
  ): Promise<ReflogEntry[]> {
    return getJson(
      `/api/repositories/${encodePath(slug)}/reflogs/${encodePath(
        name,
      )}?limit=${limit}&offset=${offset}`,
    );
  },
  deleteRef(slug: string, name: string): Promise<RefDeletion> {
    return sendJson(
      `/api/repositories/${encodePath(slug)}/refs/${encodePath(name)}`,
      "DELETE",
    );
  },
  removeRun(slug: string, id: string): Promise<{ id: string }> {
    return sendJson(
      `/api/repositories/${encodePath(slug)}/runs/${encodePath(id)}`,
      "DELETE",
    );
  },
};
