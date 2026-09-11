import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { reportUiError } from "@/lib/errors";

interface Props {
  /** Where the boundary sits; becomes the `source` of the logged error. */
  scope: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    reportUiError(`boundary:${this.props.scope}`, error, info.componentStack ?? undefined);
  }

  override render() {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div role="alert" className="m-6 space-y-3 rounded-md border border-danger/40 p-4">
        <h2 className="text-sm font-semibold">This view stopped working</h2>
        <p className="text-sm text-muted-foreground">{error.message}</p>
        <p className="text-xs text-muted-foreground">
          Technical details were written to the application log.
        </p>
        <Button size="sm" variant="outline" onClick={() => this.setState({ error: null })}>
          Try again
        </Button>
      </div>
    );
  }
}
