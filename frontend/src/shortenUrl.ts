// Fitting a repository URL into a narrow column.

const GAP = "/…/";

/** A URL shortened to about `max` characters by dropping path segments from the
 *  middle.
 *
 *  What identifies a repository sits at both ends. The scheme and host say where
 *  it lives, and the last segments say which one it is. So the middle goes and
 *  the ends stay. A local repository under a long temp path is the case that
 *  needs it.
 *
 *  The last segment is always kept, even when it alone is longer than `max`.
 *  Callers pair this with the full URL in a `title`.
 *
 *  Pure and exported, so tests check the cut rather than a rendered sidebar. */
export function shortenUrl(url: string, max = 32): string {
  if (url.length <= max) return url;
  const scheme = url.indexOf("://");
  const pathAt = url.indexOf("/", scheme < 0 ? 0 : scheme + 3);
  if (pathAt < 0) return url;
  // The head keeps the scheme and the host. A directory path has neither, so
  // the head is empty and the gap starts at the leading slash.
  const head = url.slice(0, pathAt);
  const parts = url
    .slice(pathAt + 1)
    .split("/")
    .filter((p) => p !== "");
  const last = parts.at(-1);
  if (last === undefined) return url;
  let tail = last;
  for (const part of parts.slice(0, -1).reverse()) {
    const wider = `${part}/${tail}`;
    if (head.length + GAP.length + wider.length > max) break;
    tail = wider;
  }
  // Every segment fit, so there is nothing to drop and the gap only adds width.
  if (tail === parts.join("/")) return url;
  return `${head}${GAP}${tail}`;
}
