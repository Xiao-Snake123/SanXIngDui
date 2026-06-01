import { useNavigate } from "react-router";
import { HeroSection } from "../components/HeroSection";

export function HomePage() {
  const navigate = useNavigate();
  const scrollTo = (id: string) => {
    const routes: Record<string, string> = {
      museum: "/museum/gallery",
      "ai-restore": "/ai/scene",
      "open-source": "/developer/resources",
    };
    if (routes[id]) navigate(routes[id]);
    else {
      const el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior: "smooth" });
    }
  };
  return <HeroSection onScrollTo={scrollTo} />;
}
