import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthGuard } from "lemma-sdk/react";
import App from "./App";
import { PodAccessGuard } from "./PodAccessGuard";
import { client } from "./lemma";
import "./styles.css";
import "./arcade.css";
import "./cinematic.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthGuard client={client}>
        <PodAccessGuard>
          <App />
        </PodAccessGuard>
      </AuthGuard>
    </QueryClientProvider>
  </StrictMode>,
);
