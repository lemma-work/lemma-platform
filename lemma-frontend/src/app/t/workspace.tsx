"use client";

import { PageLoading } from "@/ui/loading";

import dynamic from "next/dynamic";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { retryTransient, transientRetryDelay } from "@/session/auth-state";

// Lemma's browser SDK, the session, and the locally selected data source all
// need a browser. Keep that boundary explicit while Next renders the document
// and the loading shell.
const App = dynamic(() => import("@/shell/app").then(m => m.App), {
    ssr: false,
    loading: () => <PageLoading label="Opening Lemma" />,
});

export function Workspace() {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: {
        retry: retryTransient, retryDelay: transientRetryDelay,
        refetchOnWindowFocus: false, staleTime: 60_000, gcTime: 30 * 60_000,
    } } }));
    /* The provider stays out here. Signing out clears the query cache, so the
       gate has to be inside one — and the gate is inside `App`, on the other
       side of the browser-only boundary. */
    return (
        <QueryClientProvider client={queryClient}>
            <App />
        </QueryClientProvider>
    );
}
