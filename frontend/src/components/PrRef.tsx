/** A PR reference. The number links to its host when a `pr_url_template` is
 *  configured, with `{pr}` replaced by the number, and is plain text otherwise.
 *  The title, when present, follows as adjacent text.
 *
 *  The link stops click propagation so opening the PR does not also select an
 *  enclosing row. */
export function PrRef({
  pr,
  title,
  template,
}: {
  pr: number;
  title?: string | null;
  template: string | null;
}) {
  const label = `pr ${pr}`;
  return (
    <>
      {template ? (
        <a
          className="pr-link"
          href={template.replace("{pr}", String(pr))}
          target="_blank"
          rel="noreferrer"
          title={`Open ${label}`}
          onClick={(e) => e.stopPropagation()}
        >
          {label}
        </a>
      ) : (
        label
      )}
      {title ? <span className="pr-title"> · {title}</span> : null}
    </>
  );
}
