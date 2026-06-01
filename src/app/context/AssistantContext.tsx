import { createContext, useContext, useState, ReactNode } from "react";

interface AssistantContextType {
  query: string;
  setQuery: (q: string) => void;
}

const AssistantContext = createContext<AssistantContextType>({ query: "", setQuery: () => {} });

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState("");
  return (
    <AssistantContext.Provider value={{ query, setQuery }}>
      {children}
    </AssistantContext.Provider>
  );
}

export const useAssistant = () => useContext(AssistantContext);
