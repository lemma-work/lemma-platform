"use client";

import dynamic from "next/dynamic";

/* Client-only for the same reason the shell is: it reads and writes this
   browser's storage, and there is no server-side answer to what is in it. */
const ConnectScreen = dynamic(() => import("@/session/connect-screen").then(m => m.ConnectScreen), {
    ssr: false,
    loading: () => <div className="screen"><div className="screen__inner"><p role="status">Opening…</p></div></div>,
});

export default function ConnectPage() {
    return <ConnectScreen />;
}
