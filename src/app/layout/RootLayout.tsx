import { Outlet, useLocation } from "react-router";
import { useEffect } from "react";
import { ParticleBackground } from "../components/ParticleBackground";
import { Navbar } from "../components/Navbar";
import { FloatingAssistant } from "../components/FloatingAssistant";

export function RootLayout() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [pathname]);

  return (
    <div
      style={{
        background: "#0B0C10",
        color: "#C5C6C7",
        fontFamily: "'Noto Sans SC', sans-serif",
        minHeight: "100vh",
        overflowX: "hidden",
        position: "relative",
      }}
    >
      <ParticleBackground />
      <Navbar />
      <main style={{ position: "relative", zIndex: 1 }}>
        <Outlet />
      </main>
      <FloatingAssistant />
    </div>
  );
}
