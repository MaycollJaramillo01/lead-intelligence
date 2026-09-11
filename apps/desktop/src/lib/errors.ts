import { desktop } from "@/lib/ipc";

const clip = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

function asError(value: unknown): Error {
  if (value instanceof Error) return value;
  if (typeof value === "string") return new Error(value);
  return new Error(JSON.stringify(value) ?? String(value));
}

/** Logs a UI failure in the console and forwards it to the desktop log file. */
export function reportUiError(source: string, error: unknown, context?: string): void {
  const err = asError(error);
  console.error(`[${source}]`, err);
  const stack = [err.stack, context].filter(Boolean).join("\n");
  desktop
    .reportUiError({
      source: clip(source, 64),
      message: clip(err.message || err.name, 2000),
      stack: stack ? clip(stack, 8000) : null,
    })
    .catch((forwardError: unknown) => {
      console.error("[reportUiError] could not forward the error to the desktop log", forwardError);
    });
}

export function installGlobalErrorReporting(): void {
  window.addEventListener("error", (event) =>
    reportUiError("window.error", event.error ?? event.message),
  );
  window.addEventListener("unhandledrejection", (event) =>
    reportUiError("unhandledrejection", event.reason),
  );
}
