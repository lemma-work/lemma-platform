"use client";

import dynamic from "next/dynamic";

/** The browser-only boundary, held in a client component because that is the
 *  only place `ssr: false` is allowed — the same split `t/workspace.tsx` and
 *  `auth/portal-host.tsx` make, and for the same reason.
 *
 *  Browser-only in earnest: the session, the SDK client and a VNC socket all
 *  need a browser, and none of them has an answer on a server.
 */
const Arrival = dynamic(() => import("@/computer/sign-in-arrival").then((m) => m.SignInArrival), {
    ssr: false,
    loading: () => <div className="screen"><div className="screen__inner"><p role="status">Opening…</p></div></div>,
});

export function SignInHost({ conversationId, toolCallId }: { conversationId: string; toolCallId: string }) {
    return <Arrival conversationId={conversationId} toolCallId={toolCallId} />;
}
