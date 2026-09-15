import { toast } from "sonner";
import { PageScroll, PageShell, Section } from "@/components/layout/page-chrome";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { useT } from "@/lib/i18n";
import {
  usePreferences,
  type Density,
  type Language,
  type ThemePreference,
} from "@/stores/preferences";

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <Label htmlFor={id} className="text-xs font-normal">
        {label}
      </Label>
      {children}
    </div>
  );
}

export function SettingsScreen() {
  const t = useT();
  const theme = usePreferences((state) => state.theme);
  const density = usePreferences((state) => state.density);
  const language = usePreferences((state) => state.language);
  const setTheme = usePreferences((state) => state.setTheme);
  const setDensity = usePreferences((state) => state.setDensity);
  const setLanguage = usePreferences((state) => state.setLanguage);
  const reset = usePreferences((state) => state.reset);
  const showAdvancedTools = usePreferences((state) => state.showAdvancedTools);
  const setShowAdvancedTools = usePreferences((state) => state.setShowAdvancedTools);
  const core = useDiagnosticsStore((state) => state.report?.core ?? null);

  return (
    <PageShell>
      <PageScroll>
        <Section title={t("settings.appearance")} description={t("settings.appearanceHelp")}>
          <div className="divide-y divide-border">
            <Field id="theme" label={t("common.theme")}>
              <Select value={theme} onValueChange={(value) => setTheme(value as ThemePreference)}>
                <SelectTrigger id="theme" size="sm" className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="system">{t("theme.system")}</SelectItem>
                  <SelectItem value="light">{t("theme.light")}</SelectItem>
                  <SelectItem value="dark">{t("theme.dark")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="density" label={t("common.density")}>
              <Select value={density} onValueChange={(value) => setDensity(value as Density)}>
                <SelectTrigger id="density" size="sm" className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="comfortable">{t("density.comfortable")}</SelectItem>
                  <SelectItem value="compact">{t("density.compact")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="language" label={t("common.language")}>
              <Select value={language} onValueChange={(value) => setLanguage(value as Language)}>
                <SelectTrigger id="language" size="sm" className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="en">{t("language.en")}</SelectItem>
                  <SelectItem value="es">{t("language.es")}</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="advanced-tools" label={t("settings.advancedTools")}>
              <Switch
                id="advanced-tools"
                checked={showAdvancedTools}
                onCheckedChange={setShowAdvancedTools}
                aria-describedby="advanced-tools-help"
              />
            </Field>
            <p id="advanced-tools-help" className="py-2 text-xs text-muted-foreground">
              {t("settings.advancedToolsHelp")}
            </p>
          </div>
        </Section>

        <Section title={t("settings.workspace")} description={t("settings.workspaceHelp")}>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
            <dt className="text-muted-foreground">{t("settings.dataDir")}</dt>
            <dd className="font-mono break-all">{core?.data_dir ?? t("common.unknown")}</dd>
            <dt className="text-muted-foreground">{t("settings.logDir")}</dt>
            <dd className="font-mono break-all">{core?.log_dir ?? t("common.unknown")}</dd>
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">{t("settings.pathsHelp")}</p>
        </Section>

        <Section title={t("settings.preferences")} description={t("settings.preferencesHelp")}>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              reset();
              toast.success(t("settings.resetDone"));
            }}
          >
            {t("settings.resetButton")}
          </Button>
        </Section>
      </PageScroll>
    </PageShell>
  );
}
