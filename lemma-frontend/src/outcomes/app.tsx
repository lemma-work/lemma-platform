"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { SessionGate } from "@/session/session";
import { keepOnDevice, restoreFromDevice } from "@/session/device-cache";
import { OutcomesShell } from "./shell";
import { StartScreen } from "./start-screen";

/** The gate and the frame it guards, behind one browser-only boundary, for
 *  the same reason `shell/app.tsx` keeps them together. What the device kept
 *  from the last visit is put back while the gate asks who is signed in, so
 *  the frame opens on it, and the network catches up behind.
 *
 *  Every wait on the way -- the session, the API not answering, the device
 *  -- draws the one start-up screen, so opening the app is one screen that
 *  fills in rather than a run of sentences. A session that ends while the
 *  frame is open keeps the frame, with a dialog over it. */
export function OutcomesApp() {
    const restored = useDeviceCache();
    return (
        <SessionGate
            staysOnSignOut
            starting={(words) => <StartScreen words={words} />}
            unreachable={({ seconds, tryNow }) => <StartScreen down={{ seconds, onTryNow: tryNow }} />}
        >
            {restored ? <OutcomesShell /> : <StartScreen words="Opening Lemma" />}
        </SessionGate>
    );
}

/** Whether what the device kept has been put back -- straight away where
 *  there is nothing to keep: sample data is nobody's. */
function useDeviceCache(): boolean {
    const client = useQueryClient();
    const live = source.label === "live";
    const [restored, setRestored] = useState(!live);
    useEffect(() => {
        if (!live) return;
        let stop: (() => void) | null = null;
        let gone = false;
        void restoreFromDevice(client).finally(() => {
            if (gone) return;
            /* Kept from here on, and not before: what was just put back
               needs no writing again. */
            stop = keepOnDevice(client);
            setRestored(true);
        });
        return () => {
            gone = true;
            stop?.();
        };
    }, [client, live]);
    return restored;
}
