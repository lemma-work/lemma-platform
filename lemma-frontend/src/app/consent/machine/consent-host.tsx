"use client";

import { PageLoading } from "@/ui/loading";

import dynamic from "next/dynamic";

/** The browser-only boundary, for the same reason `sign-in-to-site` has one:
 *  the session, the SDK client and the window that opened this tab all need a
 *  browser, and none of them has an answer on a server. */
const Arrival = dynamic(() => import("@/machines/consent-screen").then((m) => m.MachineConsentArrival), {
    ssr: false,
    loading: () => <PageLoading label="Opening app permission" />,
});

export function MachineConsentHost() {
    return <Arrival />;
}
