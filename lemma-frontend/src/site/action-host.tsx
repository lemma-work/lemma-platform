"use client";
import dynamic from "next/dynamic";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
const Actions = dynamic(() => import("./actions").then((m) => m.Actions), {
    ssr: false,
    loading: () => <p role="status">Opening…</p>,
});
export type ActionProps = {
    action: "import" | "invite" | "logout" | "remix" | "organization";
    owner?: string;
    repo?: string;
    invitationId?: string;
    decision?: "accept" | "reject";
    source?: string;
};
export function ActionHost(props: ActionProps) {
    const [client] = useState(
        () =>
            new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    );
    return (
        <QueryClientProvider client={client}>
            <Actions {...props} />
        </QueryClientProvider>
    );
}
