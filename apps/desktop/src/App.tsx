import { useState } from "react";
import { RouterProvider } from "react-router";
import { createAppRouter } from "@/app/router";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { TooltipProvider } from "@/components/ui/tooltip";

export function App() {
  const [router] = useState(createAppRouter);
  return (
    <ErrorBoundary scope="app">
      <TooltipProvider delayDuration={250}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </ErrorBoundary>
  );
}
