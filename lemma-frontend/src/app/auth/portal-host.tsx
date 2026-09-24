"use client";

import { WorkspaceLoading } from "@/shell/workspace-loading";

import dynamic from "next/dynamic";

/** The browser-only boundary, held in a client component because that is the
 *  only place `ssr: false` is allowed — the same split `t/workspace.tsx`
 *  makes, and for the same reason: the route below renders the document and
 *  this takes over after hydration.
 *
 *  The portal is browser-only in earnest. It reads a reset token out of the
 *  URL, talks to browser storage, and decides where to send somebody from
 *  `window.location`. None of that has an answer on a server.
 */
const Portal = dynamic(() => import("@/auth/portal").then((m) => m.Portal), {
    ssr: false,
    loading: () => <WorkspaceLoading />,
});

export function PortalHost({ path }: { path?: string[] }) {
    return <Portal path={path} />;
}
