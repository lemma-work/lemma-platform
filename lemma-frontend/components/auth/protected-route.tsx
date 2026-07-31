"use client";

import { useMemo, type ReactNode } from "react";
import { AuthGuard } from "lemma-sdk/react";
import { PageLoader } from "@/components/brand/loader";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { LocalAiSetupBanner } from "@/components/desktop/local-ai-setup-banner";

export function ProtectedRoute({ children }: { children: ReactNode }) {
    const client = useMemo(() => getLemmaClient(), []);

    return (
        <AuthGuard client={client} loadingFallback={<PageLoader />}>
            <LocalAiSetupBanner />
            {children}
        </AuthGuard>
    );
}
