"use client";

import { PageLoading } from "@/ui/loading";

import dynamic from "next/dynamic";

/* Client-only for the same reason the shell is: it reads and writes this
   browser's storage, and there is no server-side answer to what is in it. */
const ConnectScreen = dynamic(() => import("@/session/connect-screen").then(m => m.ConnectScreen), {
    ssr: false,
    loading: () => <PageLoading label="Opening connection settings" />,
});

export default function ConnectPage() {
    return <ConnectScreen />;
}
