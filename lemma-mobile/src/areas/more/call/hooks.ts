import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Linking, PermissionsAndroid, Platform } from "react-native";
import { useNavigation } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import type { CanvasItem } from "@web/call/canvas";
import { useCacheKey, useLemma } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { problemOf } from "../../admin/words";
import { moreKey, useMoreSource } from "../hooks";
import type { EngineHandle } from "./engine";
import { CallSession } from "./machine";
import { afterWords, callWords, captionOf, phaseOf, type FromPage, type Setup } from "./model";
import type { CallSource } from "./source";

/** A call with a Lemma, for the call screen: the web's `useHuddle` and
 *  `useCall` together, on the phone's session.
 *
 *  Before the call: can this server carry one (said plainly when not), and,
 *  on Android, the microphone. Then the call is opened on the server, told
 *  about the Lemma and the chat it grew out of, and its socket carries the
 *  rest (`machine.ts`). When it ends, its own chat holds what was asked and
 *  done. */

export function useCallSource(): CallSource {
    return useMoreSource().call;
}

/** Whether this Lemma's server can carry a call: asked before the mic is
 *  offered, as the web does, not answered after it is pressed. Undefined
 *  while the probe is out. */
export function useCallable(podId: string | null | undefined): boolean | undefined {
    const source = useCallSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: moreKey(key, "call", podId ?? "", "callable"),
        queryFn: () => source.callable(podId as string),
        enabled: Boolean(podId),
        staleTime: 5 * 60_000,
        retry: 0,
    }).data;
}

const OS: "ios" | "android" = Platform.OS === "ios" ? "ios" : "android";

export function useVoiceCall(podId: string, conversationId: string | null) {
    const source = useCallSource();
    const { server } = useSession();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "the Lemma";
    const nameRef = useRef(name);
    nameRef.current = name;

    const pageRef = useRef<EngineHandle | null>(null);
    const session = useMemo(() => new CallSession({ source, podId, post: (message) => pageRef.current?.post(message), os: OS }), [source, podId]);
    const view = useSyncExternalStore(session.subscribe, session.snapshot);
    useEffect(() => () => session.end(), [session]);

    /* ── before the call ── */
    const [setup, setSetup] = useState<Setup>({ kind: "checking" });
    /* The call's own chat, kept as it ends: what was asked and done is there. */
    const [callId, setCallId] = useState<string | null>(null);
    const attemptRef = useRef(0);
    const begin = useCallback(async () => {
        const attempt = ++attemptRef.current;
        const current = () => attempt === attemptRef.current;
        setCallId(null);
        setSetup({ kind: "checking" });
        let callable: boolean;
        try {
            callable = await source.callable(podId);
        } catch (error) {
            if (current()) setSetup({ kind: "failed", error: problemOf(error, `Couldn’t reach ${server.label}. Check the connection and call again.`) });
            return;
        }
        if (!current()) return;
        if (!callable) {
            setSetup({ kind: "unconfigured" });
            return;
        }
        /* The page's microphone rides on the app's: on Android the app asks
           first, in its own words. On iOS the system asks when the page
           first wants it. Sample data has no microphone. */
        if (source.sound && Platform.OS === "android") {
            const permission = PermissionsAndroid.PERMISSIONS.RECORD_AUDIO;
            const held = await PermissionsAndroid.check(permission).catch(() => false);
            if (!current()) return;
            if (!held) {
                setSetup({ kind: "asking" });
                const answer = await PermissionsAndroid.request(permission, {
                    title: "The microphone",
                    message: `So ${nameRef.current} can hear you, the app needs the microphone. It listens only while a call is on.`,
                    buttonPositive: "Allow",
                    buttonNegative: "Not now",
                }).catch(() => PermissionsAndroid.RESULTS.DENIED);
                if (!current()) return;
                if (answer !== PermissionsAndroid.RESULTS.GRANTED) {
                    setSetup({ kind: "refused", final: answer === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN });
                    return;
                }
            }
        }
        setSetup({ kind: "opening" });
        let opened: string;
        let brief: string;
        try {
            opened = await source.open(podId, nameRef.current);
            if (!current()) return;
            setCallId(opened);
            /* What came before the call goes with it, so it continues that
               chat rather than starting cold. */
            brief = await source.brief(podId, nameRef.current, conversationId);
        } catch (error) {
            if (current()) setSetup({ kind: "failed", error: problemOf(error, "Couldn’t start the call. Try calling again.") });
            return;
        }
        if (!current()) return;
        setSetup({ kind: "started" });
        void session.start(opened, { teammate: nameRef.current, pod: nameRef.current, context: brief });
    }, [source, session, podId, conversationId, server.label]);

    /* The call starts as the screen opens: pressing call is what opened it. */
    useEffect(() => {
        void begin();
        return () => { attemptRef.current += 1; };
    }, [begin]);

    /* ── leaving ── */
    const navigation = useNavigation();
    const [leaving, setLeaving] = useState<{ action: unknown } | null>(null);
    useEffect(() => navigation.addListener("beforeRemove", (event) => {
        if (!session.active) return;
        event.preventDefault();
        setLeaving({ action: event.data.action });
    }), [navigation, session]);
    const leave = useCallback(() => {
        const held = leaving;
        setLeaving(null);
        session.end();
        if (held) navigation.dispatch(held.action as Parameters<typeof navigation.dispatch>[0]);
    }, [leaving, session, navigation]);

    /* End call works from the first moment the screen offers it, before the
       call itself has begun too. */
    const end = useCallback(() => {
        if (session.active) {
            session.end();
            return;
        }
        attemptRef.current += 1;
        setSetup((was) => (was.kind === "opening" || was.kind === "checking" || was.kind === "asking" ? { kind: "cancelled" } : was));
    }, [session]);

    const phase = phaseOf(setup, view.status, view.error, view.cause);
    const placed = setup.kind === "started" && (phase.kind === "ended" || phase.kind === "failed" || phase.kind === "refused");

    return {
        name,
        icon: lemma?.iconUrl ?? null,
        phase,
        words: callWords(phase, name, { muted: view.muted, thinking: view.working }, OS),
        muted: view.muted,
        /* The ring's own store, so it alone redraws. */
        level: { subscribe: session.subscribeLevel, snapshot: session.levelSnapshot },
        caption: captionOf(view.transcript, name),
        canvas: view.canvas,
        /* Only for a call that was placed: one refused before it began went nowhere. */
        after: placed ? { chatId: callId, ...afterWords(callId) } : null,
        engine: source.sound && (view.status === "connecting" || view.status === "live")
            ? { ref: pageRef, onMessage: (message: FromPage) => session.hear(message) }
            : null,
        begin,
        end,
        /* Mute reaches the server and the page, so it waits for the call. */
        canMute: view.status === "connecting" || view.status === "live",
        toggleMute: session.toggleMute,
        looking: (item: CanvasItem) => session.looking(item),
        openSettings: () => void Linking.openSettings().catch(() => undefined),
        leaving: leaving !== null,
        leave,
        stay: () => setLeaving(null),
    };
}

export type VoiceCall = ReturnType<typeof useVoiceCall>;
