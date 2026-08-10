export const THEME_STORAGE_KEY = "verdict-theme";
export const THEME_ATTRIBUTE = "data-theme";

const THEME_CHANGE_EVENT = "verdict:theme-change";
let volatileThemePreference: Theme | null = null;

export type Theme = "light" | "dark";

export function isTheme(value: string | null): value is Theme {
  return value === "light" || value === "dark";
}

export function getSystemTheme(): Theme {
  if (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  ) {
    return "dark";
  }

  return "light";
}

export function getStoredTheme(): Theme | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(storedTheme) ? storedTheme : volatileThemePreference;
  } catch {
    // Storage can be unavailable in hardened or private browsing contexts.
    return volatileThemePreference;
  }
}

export function getThemeSnapshot(): Theme {
  if (typeof document === "undefined") {
    return "light";
  }

  const appliedTheme = document.documentElement.getAttribute(THEME_ATTRIBUTE);
  if (isTheme(appliedTheme)) {
    return appliedTheme;
  }

  return getStoredTheme() ?? getSystemTheme();
}

export function getServerThemeSnapshot(): Theme {
  return "light";
}

function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") {
    return;
  }

  document.documentElement.setAttribute(THEME_ATTRIBUTE, theme);
  document.documentElement.style.colorScheme = theme;
}

export function setThemePreference(theme: Theme): void {
  applyTheme(theme);
  volatileThemePreference = theme;

  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The in-memory selection still applies when persistence is unavailable.
  }

  window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
}

export function subscribeToTheme(onStoreChange: () => void): () => void {
  if (typeof window === "undefined") {
    return () => undefined;
  }

  const colorSchemeQuery =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;

  const handleThemeChange = () => {
    onStoreChange();
  };

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== THEME_STORAGE_KEY && event.key !== null) {
      return;
    }

    volatileThemePreference = isTheme(event.newValue) ? event.newValue : null;
    const nextTheme = volatileThemePreference ?? getSystemTheme();
    applyTheme(nextTheme);
    onStoreChange();
  };

  const handleSystemThemeChange = (event: MediaQueryListEvent) => {
    if (getStoredTheme() !== null) {
      return;
    }

    applyTheme(event.matches ? "dark" : "light");
    onStoreChange();
  };

  window.addEventListener(THEME_CHANGE_EVENT, handleThemeChange);
  window.addEventListener("storage", handleStorage);
  colorSchemeQuery?.addEventListener("change", handleSystemThemeChange);

  return () => {
    window.removeEventListener(THEME_CHANGE_EVENT, handleThemeChange);
    window.removeEventListener("storage", handleStorage);
    colorSchemeQuery?.removeEventListener("change", handleSystemThemeChange);
  };
}

/**
 * Runs in the document head before the body is painted. Keep this dependency-free:
 * it is serialized directly into the root layout rather than shipped as a bundle.
 */
export const THEME_BOOTSTRAP_SCRIPT = `(() => {
  const root = document.documentElement;
  let storedTheme = null;
  try {
    storedTheme = window.localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
  } catch {}
  const theme = storedTheme === "light" || storedTheme === "dark"
    ? storedTheme
    : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  root.setAttribute(${JSON.stringify(THEME_ATTRIBUTE)}, theme);
  root.style.colorScheme = theme;
})();`;
