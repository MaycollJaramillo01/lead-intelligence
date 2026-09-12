import { usePreferences, type ThemePreference } from "@/stores/preferences";

export function resolveTheme(preference: ThemePreference, prefersDark: boolean): "light" | "dark" {
  return preference === "system" ? (prefersDark ? "dark" : "light") : preference;
}

interface ThemeQuery {
  matches: boolean;
  addEventListener?: (type: "change", listener: () => void) => void;
  removeEventListener?: (type: "change", listener: () => void) => void;
}

function darkModeQuery(): ThemeQuery {
  // jsdom and older webviews may not implement matchMedia
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : { matches: false };
}

/**
 * Mirrors appearance preferences onto <html> (theme class, density and language attributes)
 * and keeps them in sync with the OS theme. Returns a cleanup function.
 */
export function installAppearanceSync(): () => void {
  const query = darkModeQuery();
  const apply = () => {
    const { theme, density, language } = usePreferences.getState();
    const root = document.documentElement;
    root.classList.toggle("dark", resolveTheme(theme, query.matches) === "dark");
    root.dataset.density = density;
    root.lang = language;
  };
  apply();
  const unsubscribe = usePreferences.subscribe(apply);
  query.addEventListener?.("change", apply);
  return () => {
    unsubscribe();
    query.removeEventListener?.("change", apply);
  };
}
