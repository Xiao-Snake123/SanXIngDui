import { createBrowserRouter, Navigate } from "react-router";
import { RootLayout } from "./layout/RootLayout";
import { HomePage } from "./pages/HomePage";
import { MuseumPage } from "./pages/museum/MuseumPage";
import { AIPage } from "./pages/ai/AIPage";
import { CulturePage } from "./pages/culture/CulturePage";
import { DeveloperPage } from "./pages/developer/DeveloperPage";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    children: [
      { index: true, Component: HomePage },
      { path: "museum", element: <Navigate to="/museum/gallery" replace /> },
      { path: "museum/:tab", Component: MuseumPage },
      { path: "ai", element: <Navigate to="/ai/scene" replace /> },
      { path: "ai/:tab", Component: AIPage },
      { path: "culture", element: <Navigate to="/culture/essence" replace /> },
      { path: "culture/:tab", Component: CulturePage },
      { path: "developer", element: <Navigate to="/developer/resources" replace /> },
      { path: "developer/:tab", Component: DeveloperPage },
    ],
  },
]);
