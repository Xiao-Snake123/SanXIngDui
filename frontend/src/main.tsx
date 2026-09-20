import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";
import "./styles/index.css";

// StrictMode 会在开发环境下双调用 effect，专门用来暴露「副作用没清理干净」的问题
// —— 本项目的 SSE 连接（AbortController）与各类定时器都依赖 useEffect 清理，
// 恰恰是最需要它的场景。生产构建下不会双调用，无副作用。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
