import { useMemo } from "react";
import { usePreferences, type Language } from "@/stores/preferences";
import { en } from "@/lib/locales/en";
import { es } from "@/lib/locales/es";

export type TranslationKey = keyof typeof en;
export type TranslationVars = Record<string, string | number>;

const DICTIONARIES: Record<Language, Partial<Record<TranslationKey, string>>> = { en, es };

/** Looks up a key, falling back to English and finally to the key itself. */
export function translate(language: Language, key: TranslationKey, vars?: TranslationVars): string {
  const template = DICTIONARIES[language][key] ?? en[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

export type Translator = (key: TranslationKey, vars?: TranslationVars) => string;

export function useT(): Translator {
  const language = usePreferences((state) => state.language);
  return useMemo(
    () => (key: TranslationKey, vars?: TranslationVars) => translate(language, key, vars),
    [language],
  );
}

export function useLanguage(): Language {
  return usePreferences((state) => state.language);
}

export function formatDateTime(value: string | number | Date, language: Language): string {
  return new Intl.DateTimeFormat(language === "es" ? "es-ES" : "en-GB", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}
