/** The evaltrack wordmark. The mark is recreated inline so it tracks the theme
 *  (accent/pass) and stays crisp. See assets/wordmark.svg. */
export function Brand() {
  return (
    <h1 className="brand">
      <svg className="brand-mark" viewBox="0 4 52 44" aria-hidden="true">
        <polyline
          points="6,28 18,40 46,12"
          fill="none"
          stroke="var(--accent)"
          strokeWidth="6.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="6" cy="28" r="3.8" fill="var(--accent)" />
        <circle cx="46" cy="12" r="4.6" fill="var(--pass)" />
      </svg>
      <span className="brand-text">
        <span className="brand-eval">eval</span>
        <span className="brand-track">track</span>
      </span>
    </h1>
  );
}
