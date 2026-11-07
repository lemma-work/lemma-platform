"use client";

import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionGate } from "@/session/session";
import { Screen } from "@/auth/screens";
import { LoadingIndicator } from "@/ui/loading";
import { appIdFromSearch, finish } from "./consent-model";
import { useGrantMachineConsent, useMachineConsents } from "./consent";

/** `/consent/machine?app=<id>`: a person saying whether an app may run
 *  commands on their machine. `consent.ts` has why this is asked here. */
export function MachineConsentArrival() {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: {
        retry: false, refetchOnWindowFocus: false, staleTime: 60_000,
    } } }));
    return (
        <QueryClientProvider client={queryClient}>
            <SessionGate>
                <MachineConsentScreen />
            </SessionGate>
        </QueryClientProvider>
    );
}

type Answered = { granted: boolean; appName: string | null };

function MachineConsentScreen() {
    const [appId] = useState(() => appIdFromSearch(window.location.search));
    /* The page that sent somebody here, as the browser reports it — not as
       the link claims. The app writes the link; it cannot write this. */
    const [from] = useState(() => hostOf(document.referrer));
    const consents = useMachineConsents(Boolean(appId));
    const grant = useGrantMachineConsent();
    const [answered, setAnswered] = useState<Answered | null>(null);

    const already = consents.data?.find((consent) => consent.app_id === appId) ?? null;

    useEffect(() => {
        if (answered && appId) finish(appId, answered.granted);
    }, [answered, appId]);

    if (!appId) {
        return (
            <Screen
                title="This link is incomplete"
                lead="It does not say which app is asking. Go back to the app and try again from there."
            />
        );
    }

    if (answered) {
        return (
            <Screen
                title={answered.granted ? "Allowed" : "Not allowed"}
                lead={answered.granted
                    ? (answered.appName ?? "The app") + " can now run commands on your machine. You can close this tab and go back to it."
                    : "Nothing was changed. You can close this tab."}
                footer={answered.granted ? "You can take this back at any time in Settings, under Apps on your machine." : undefined}
            />
        );
    }

    if (consents.isPending) {
        return (
            <Screen title="Checking…">
                <LoadingIndicator label="Checking what this app may do" />
            </Screen>
        );
    }

    const allow = () => {
        grant.mutate(appId, {
            onSuccess: (consent) => setAnswered({ granted: true, appName: consent.app_name ?? null }),
        });
    };

    const name = already?.app_name ?? "An app";
    return (
        <Screen
            title={already ? name + " can already use your machine" : name + " wants to run commands on your machine"}
            lead={
                <>
                    It would run as you: start programs, read and change files, and use the browser on your
                    machine{from ? <>. The request came from <strong>{from}</strong></> : null}. Only allow an app
                    you opened yourself and trust.
                </>
            }
            footer="You can take this back at any time in Settings, under Apps on your machine."
        >
            {grant.isError && (
                <p className="auth__problem" role="alert">
                    {grant.error instanceof Error ? grant.error.message : "That did not work. Try again."}
                </p>
            )}
            <div className="screen__actions">
                <button className="btn btn--primary" disabled={grant.isPending} onClick={allow}>
                    {grant.isPending ? "Allowing…" : already ? "Keep allowing" : "Allow"}
                </button>
                <button className="btn" disabled={grant.isPending} onClick={() => setAnswered({ granted: false, appName: null })}>
                    Don’t allow
                </button>
            </div>
        </Screen>
    );
}

function hostOf(url: string): string | null {
    try {
        return url ? new URL(url).host : null;
    } catch {
        return null;
    }
}
