"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionGate } from "@/session/session";
import { SignInPane } from "./sign-in-pane";

export function SignInArrival({ conversationId, toolCallId }: { conversationId: string; toolCallId: string }) {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: {
        retry: false, refetchOnWindowFocus: false, staleTime: 60_000,
    } } }));

    return (
        <QueryClientProvider client={queryClient}>
            <SessionGate>
                <main className="signin-page">
                    <SignInPane conversationId={conversationId} toolCallId={toolCallId} />
                </main>
            </SessionGate>
        </QueryClientProvider>
    );
}
