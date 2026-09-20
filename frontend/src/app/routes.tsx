import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router";
import { RootLayout } from "./layout/RootLayout";

// 四个页面改为按需加载。
//
// 原先是静态 import：用户只访问 /ai 也要把 museum / culture / developer
// 三个页面（合计 2800+ 行、含大量硬编码数据）一起下载完才能渲染。
// 拆开之后首屏只加载当前路由需要的那一份。
const HomePage = lazy(() => import("./pages/HomePage").then((m) => ({ default: m.HomePage })));
const MuseumPage = lazy(() =>
  import("./pages/museum/MuseumPage").then((m) => ({ default: m.MuseumPage })),
);
const AIPage = lazy(() => import("./pages/ai/AIPage").then((m) => ({ default: m.AIPage })));
const CulturePage = lazy(() =>
  import("./pages/culture/CulturePage").then((m) => ({ default: m.CulturePage })),
);
const DeveloperPage = lazy(() =>
  import("./pages/developer/DeveloperPage").then((m) => ({ default: m.DeveloperPage })),
);

function PageLoading() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 12,
        color: "#556372",
      }}
    >
      加载中…
    </div>
  );
}

const withSuspense = (element: React.ReactNode) => (
  <Suspense fallback={<PageLoading />}>{element}</Suspense>
);

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    children: [
      { index: true, element: withSuspense(<HomePage />) },
      { path: "museum", element: <Navigate to="/museum/gallery" replace /> },
      { path: "museum/:tab", element: withSuspense(<MuseumPage />) },
      { path: "ai", element: <Navigate to="/ai/scene" replace /> },
      { path: "ai/:tab", element: withSuspense(<AIPage />) },
      { path: "culture", element: <Navigate to="/culture/essence" replace /> },
      { path: "culture/:tab", element: withSuspense(<CulturePage />) },
      { path: "developer", element: <Navigate to="/developer/runs" replace /> },
      { path: "developer/:tab", element: withSuspense(<DeveloperPage />) },
    ],
  },
]);
