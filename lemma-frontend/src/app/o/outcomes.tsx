"use client";

import { PageLoading } from "@/ui/loading";

import dynamic from "next/dynamic";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { retryTransient, transientRetryDelay } from "@/session/auth-state";

// The SDK, the session and the data source all need a browser, as in `/t`.
const App = dynamic(() => import("@/outcomes/app").then(m => m.OutcomesApp), {
    ssr: false,
    loading: () => <PageLoading label="Opening Lemma" />,
});

export function Outcomes() {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: {
        retry: retryTransient, retryDelay: transientRetryDelay,
        refetchOnWindowFocus: false, staleTime: 60_000, gcTime: 30 * 60_000,
    } } }));
    return (
        <QueryClientProvider client={queryClient}>
            <App />
        </QueryClientProvider>
    );
}
