import { createHashRouter, createMemoryRouter, Navigate, type RouteObject } from "react-router";
import { ModuleRoute } from "@/app/ModuleRoute";
import { DEFAULT_ROUTE, NAV_ITEMS } from "@/app/navigation";
import { AppLayout } from "@/components/layout/AppLayout";
import { NotFoundScreen } from "@/features/placeholder/NotFoundScreen";

export const appRoutes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to={DEFAULT_ROUTE} replace /> },
      ...NAV_ITEMS.map((item) => ({
        path: item.path.slice(1),
        element: <ModuleRoute item={item} />,
      })),
      { path: "*", element: <NotFoundScreen /> },
    ],
  },
];

/** Hash routing: the packaged app is served from a custom protocol with no server rewrites. */
export const createAppRouter = () => createHashRouter(appRoutes);

export const createTestRouter = (initialEntries: string[] = [DEFAULT_ROUTE]) =>
  createMemoryRouter(appRoutes, { initialEntries });
