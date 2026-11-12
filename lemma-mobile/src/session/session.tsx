import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { liveAreas, sampleAreas, type Areas } from "../areas";
import { liveSource } from "../data/live";
import { setLiveEnabled } from "../live/pod-live";
import { sampleSource } from "../data/sample";
import type { LemmaSource } from "../data/source";
import { installFetch, loadSession, onSession, signOut as revoke, signedIn, type Tokens } from "./auth";
import { forgetClients } from "./client";
import { HOSTED, serverFrom, type Server } from "./server";
import { store } from "./store";

/** Who the app is talking to and as whom: signed out, signed in to a
 *  server, or showing sample data. The one place that decides which
 *  `LemmaSource` every screen reads. */

export type Mode = "loading" | "signed-out" | "live" | "sample";

interface Session {
    mode: Mode;
    server: Server;
    source: LemmaSource | null;
    /** The same, area by area (`areas/index.ts`). */
    areas: Areas | null;
    setServer: (server: Server) => Promise<void>;
    /** The sign-in WebView minted a session for this app. */
    completeSignIn: (tokens: Tokens) => Promise<void>;
    chooseSample: () => Promise<void>;
    signOut: () => Promise<void>;
    /** Bumped when the WebViews should drop their own cookie session too. */
    webSignOuts: number;
}

const SessionContext = createContext<Session | null>(null);

installFetch();

export function SessionProvider({ children }: { children: ReactNode }) {
    const [mode, setMode] = useState<Mode>("loading");
    const [server, setServerState] = useState<Server>(HOSTED);
    const [webSignOuts, setWebSignOuts] = useState(0);
    const cache = useQueryClient();

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            const chosen = serverFrom(await store.server());
            const held = await store.mode();
            const tokens = await loadSession(chosen);
            if (cancelled) return;
            setServerState(chosen);
            setMode(held === "sample" ? "sample" : tokens ? "live" : "signed-out");
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => onSession((event) => {
        if (event === "signed-out") {
            forgetClients();
            cache.clear();
            setMode((was) => (was === "sample" ? was : "signed-out"));
        }
    }), [cache]);

    const setServer = useCallback(async (next: Server) => {
        await store.setServer(JSON.stringify(next));
        const tokens = await loadSession(next);
        /* Choosing a server leaves sample data: the choice is to talk to it. */
        await store.setMode(tokens ? "live" : null);
        forgetClients();
        cache.clear();
        setServerState(next);
        setMode(tokens ? "live" : "signed-out");
    }, [cache]);

    const completeSignIn = useCallback(async (tokens: Tokens) => {
        await signedIn(server, tokens);
        await store.setMode("live");
        forgetClients();
        cache.clear();
        setMode("live");
    }, [server, cache]);

    const chooseSample = useCallback(async () => {
        await store.setMode("sample");
        cache.clear();
        setMode("sample");
    }, [cache]);

    const signOut = useCallback(async () => {
        if (mode === "sample") {
            await store.setMode(null);
            cache.clear();
            const tokens = await loadSession(server);
            setMode(tokens ? "live" : "signed-out");
            return;
        }
        await revoke();
        await store.setMode(null);
        setWebSignOuts((was) => was + 1);
        forgetClients();
        cache.clear();
        setMode("signed-out");
    }, [mode, server, cache]);

    const source = useMemo<LemmaSource | null>(() => {
        if (mode === "sample") return sampleSource();
        if (mode === "live") return liveSource(server);
        return null;
    }, [mode, server]);

    /* Live sockets only for a real session, and new ones for a new server:
       an open socket would otherwise stay with the server just left. */
    useEffect(() => {
        setLiveEnabled(false);
        if (mode === "live") setLiveEnabled(true);
    }, [mode, server.id]);

    const areas = useMemo<Areas | null>(() => {
        if (mode === "sample") return sampleAreas();
        if (mode === "live") return liveAreas(server);
        return null;
    }, [mode, server]);

    const value = useMemo<Session>(
        () => ({ mode, server, source, areas, setServer, completeSignIn, chooseSample, signOut, webSignOuts }),
        [mode, server, source, areas, setServer, completeSignIn, chooseSample, signOut, webSignOuts],
    );
    return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
    const session = useContext(SessionContext);
    if (!session) throw new Error("useSession outside SessionProvider");
    return session;
}

/** The source, for a screen that only renders once there is one. */
export function useSource(): LemmaSource {
    const { source } = useSession();
    if (!source) throw new Error("No source: the app is signed out");
    return source;
}
