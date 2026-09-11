import { useEffect, useState } from "react";

/** Serialize a value for the clipboard. A structure becomes indented JSON. A
 *  plain string is copied as itself, because the viewer also shows strings
 *  unquoted. */
function serialize(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    // A cyclic or unserializable value still copies, as its string form.
    return String(value);
  }
}

/** Copy a value to the clipboard. The label becomes a short confirmation, and
 *  a refused clipboard says so instead of doing nothing. */
export function CopyButton({ value }: { value: unknown }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 1500);
    return () => clearTimeout(t);
  }, [state]);

  return (
    <button
      type="button"
      className="copy-button"
      title="Copy to clipboard"
      onClick={() => {
        void navigator.clipboard
          .writeText(serialize(value))
          .then(() => setState("copied"))
          .catch(() => setState("failed"));
      }}
    >
      {state === "idle"
        ? "Copy"
        : state === "copied"
          ? "Copied"
          : "Copy failed"}
    </button>
  );
}
