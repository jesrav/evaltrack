import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "evaltrack-theme";

/** The OS preference, the default until the user picks a theme here. */
function preferredTheme(): Theme {
  return typeof matchMedia === "function" &&
    matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Where the chosen theme is remembered between visits. */
export interface ThemeStore {
  read(): string | null;
  write(value: string): void;
}

const browserStore: ThemeStore = {
  read: () => localStorage.getItem(STORAGE_KEY),
  write: (value) => localStorage.setItem(STORAGE_KEY, value),
};

/** A browser set to block site data still has a `localStorage` object, and
 *  throws on the access instead. Every touch is guarded, so blocked storage
 *  loses the remembered theme and nothing else. */
function readStored(store: ThemeStore): Theme | null {
  try {
    const v = store.read();
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null;
  }
}

/** The theme to start on, the remembered choice or else the OS preference. */
export function resolveTheme(store: ThemeStore = browserStore): Theme {
  return readStored(store) ?? preferredTheme();
}

/** Remember a choice for the next visit, if the browser allows it. */
export function rememberTheme(
  theme: Theme,
  store: ThemeStore = browserStore,
): void {
  try {
    store.write(theme);
  } catch {
    // The theme still applies to this page. Only the memory of it is lost.
  }
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

/** Apply the theme synchronously before React renders, so a dark-mode user
 *  doesn't see a light flash on every page load. An explicit choice in
 *  localStorage wins. With none, the OS preference decides. */
export function initTheme(): void {
  applyTheme(resolveTheme());
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(resolveTheme);

  // The click persists the theme, not this effect. A write on mount freezes
  // the OS default into an explicit choice the user never made.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Track the OS while the user hasn't picked a theme here. The check is inside
  // the handler, not the effect, so a pick made after mount takes effect too.
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const query = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (readStored(browserStore)) return;
      setTheme(query.matches ? "dark" : "light");
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => {
        rememberTheme(next);
        setTheme(next);
      }}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}
