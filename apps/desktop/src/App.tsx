import { ErrorBoundary } from "@/components/ErrorBoundary";
import { DiagnosticsScreen } from "@/features/diagnostics/DiagnosticsScreen";

export function App() {
  return (
    <ErrorBoundary scope="app">
      <DiagnosticsScreen />
    </ErrorBoundary>
  );
}
