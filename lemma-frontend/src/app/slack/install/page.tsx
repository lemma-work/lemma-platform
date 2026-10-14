"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageLoading } from "@/ui/loading";

/* Browser-only, like the workspace: the session and the SDK need one. */
const SlackInstallScreen = dynamic(
    () => import("@/slack/install-screen").then((m) => m.SlackInstallScreen),
    { ssr: false, loading: () => <PageLoading label="Opening Lemma" /> },
);

export default function SlackInstallPage() {
    const [client] = useState(() => new QueryClient());
    return (
        <QueryClientProvider client={client}>
            <SlackInstallScreen />
        </QueryClientProvider>
    );
}
