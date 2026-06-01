import { RouterProvider } from "react-router";
import { router } from "./routes";
import { AssistantProvider } from "./context/AssistantContext";

export default function App() {
  return (
    <AssistantProvider>
      <RouterProvider router={router} />
    </AssistantProvider>
  );
}
