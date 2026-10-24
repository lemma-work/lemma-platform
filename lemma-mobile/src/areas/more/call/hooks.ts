import { useCallback, useEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore } from "react";
import { Linking, PermissionsAndroid, Platform } from "react-native";
import { useNavigation } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { resourceLabel, type DisplayResource } from "@web/thread/display-resource";
import { buildTurns, type PlanStepState, type RawMessage } from "@web/thread/turns";
import { problemOf } from "../../admin/words";
import { useMoreSource } from "../hooks";
import type { EngineHandle } from "./engine";
import { CallMachine, type CallHandlers } from "./machine";
import { afterWords, callWords, captionOf, phaseOf, type FromPage, type Setup } from "./model";
import type { CallSession, CallSource, EngineLink } from "./source";
import { historyBrief } from "@web/call/history-brief";

/** A call with a Lemma, for the call screen: the web's `useHuddle` and
 *  `useCallSession` together, on the phone's session.
 *
 *  Before the call: can this server carry one (said plainly when not), and,
 *  on Android, the microphone. Then the router opens on the chat the call is
 *  about, the call starts (`machine.ts`), and what the Lemma puts on screen
 *  in that chat is drawn as it arrives and named to the voice once. When
 *  the call ends the router lets go of the chats, never the work. */

export interface CallResource {
    id: string;
    toolCallId?: string;
    resource: DisplayResource;
    label: string;
}

export function useCallSource(): CallSource {
    return useMoreSource().call;
}

const OS: "ios" | "android" = Platform.OS === "ios" ? "ios" : "android";

/** What the chat the call is about holds now: what is on screen, the plan. */
function focusedOf(session: CallSession): { messages: RawMessage[]; resources: CallResource[]; plan: PlanStepState[] } {
    const messages = [...(session.messages.get(session.focusedId ?? "") ?? [])];
    const items = buildTurns(messages).flatMap((turn) => turn.items);
    const resources = items.flatMap<CallResource>((item) => item.kind === "resource"
        ? [{ id: item.id, toolCallId: item.toolCallId, resource: item.resource, label: resourceLabel(item.resource) }]
        : []);
    const plan = items.reduce<PlanStepState[]>((found, item) => (item.kind === "plan" ? item.steps : found), []);
    return { messages, resources, plan };
}

export function useVoiceCall(podId: string, conversationId: string | null) {
    const source = useCallSource();
    const { server } = useSession();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "the Lemma";
    const nameRef = useRef(name);
    nameRef.current = name;

    const [, render] = useReducer((n: number) => n + 1, 0);
    const session = useMemo(() => source.session(podId, render), [source, podId]);
    useEffect(() => () => session.close(), [session]);

    /* The page, live; a scripted voice, a fresh one per call, in sample data. */
    const webRef = useRef<EngineHandle | null>(null);
    const engine = useMemo<EngineLink>(() => {
        const make = source.engine;
        if (!make) return { open: () => undefined, post: (message) => webRef.current?.post(message), close: () => undefined };
        let current: EngineLink | null = null;
        return {
            open(hear) {
                current = make(podId);
                current.open(hear);
            },
            post: (message) => current?.post(message),
            close() {
                current?.close();
                current = null;
            },
        };
    }, [source, podId]);

    const handlersRef = useRef<CallHandlers | null>(null);
    handlersRef.current = {
        observeTranscript: session.observeTranscript,
        route: session.route,
        subscribe: session.subscribe,
        history: () => historyBrief(buildTurns([...(session.messages.get(session.focusedId ?? "") ?? [])]), nameRef.current),
        context: async () => {
            const brief = await source.brief(podId, nameRef.current);
            session.setContext(brief);
            return brief;
        },
    };
    const machine = useMemo(() => new CallMachine({ handlers: () => handlersRef.current as CallHandlers, engine, os: OS }), [engine]);
    const view = useSyncExternalStore(machine.subscribe, machine.snapshot);
    useEffect(() => () => machine.end(), [machine]);

    /* ── before the call ── */
    const [setup, setSetup] = useState<Setup>({ kind: "checking" });
    /* Where the call's chat is, kept as it ends: closing the router forgets it. */
    const [after, setAfter] = useState<{ id: string; title: string | null } | null>(null);
    const attemptRef = useRef(0);
    const begin = useCallback(async () => {
        const attempt = ++attemptRef.current;
        const current = () => attempt === attemptRef.current;
        setAfter(null);
        setSetup({ kind: "checking" });
        let readiness;
        try {
            readiness = await source.readiness();
        } catch (error) {
            if (current()) setSetup({ kind: "failed", error: problemOf(error, `Couldn’t reach ${server.label}. Check the connection and call again.`) });
            return;
        }
        if (!current()) return;
        if (!readiness.configured) {
            setSetup({ kind: "unconfigured" });
            return;
        }
        /* The page's microphone rides on the app's: on Android the app asks
           first, in its own words. On iOS the system asks when the page
           first wants it. Sample data has no microphone. */
        if (!source.engine && Platform.OS === "android") {
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
        /* Kept, not swallowed: a call whose chat cannot be opened would talk
           and never hear back, with nothing on screen to say why. */
        try {
            await session.open(conversationId);
        } catch (error) {
            if (current()) setSetup({ kind: "failed", error: problemOf(error, "Couldn’t open the call’s chat. Try calling again.") });
            return;
        }
        if (!current()) return;
        setSetup({ kind: "started" });
        void machine.start(readiness.provider, { lemma: nameRef.current, place: nameRef.current });
    }, [source, session, machine, conversationId, server.label]);

    /* The call starts as the screen opens: pressing call is what opened it. */
    useEffect(() => {
        void begin();
        return () => { attemptRef.current += 1; };
    }, [begin]);

    /* ── during ── */
    const focused = focusedOf(session);
    const live = view.status === "live";
    const announced = useRef(new Set<string>());
    useEffect(() => {
        if (!live) {
            announced.current = new Set();
            return;
        }
        /* Once each, by kind and name: "the tickets table is on your screen"
           and "the report is on your screen" are different sentences. */
        for (const shown of focused.resources) {
            if (announced.current.has(shown.id)) continue;
            announced.current.add(shown.id);
            machine.announceWidget(`${shown.resource.type.toLowerCase()} — ${shown.label}`);
        }
    });

    /* ── after ── */
    useEffect(() => {
        if (setup.kind !== "started" || (view.status !== "ended" && view.status !== "error")) return;
        const id = session.focusedId;
        setAfter(id ? { id, title: session.snapshots.get(id)?.title ?? null } : null);
        session.close();
    }, [setup.kind, view.status, session]);

    /* ── leaving ── */
    const navigation = useNavigation();
    const [leaving, setLeaving] = useState<{ action: unknown } | null>(null);
    useEffect(() => navigation.addListener("beforeRemove", (event) => {
        if (!machine.active) return;
        event.preventDefault();
        setLeaving({ action: event.data.action });
    }), [navigation, machine]);
    const leave = useCallback(() => {
        const held = leaving;
        setLeaving(null);
        machine.end();
        if (held) navigation.dispatch(held.action as Parameters<typeof navigation.dispatch>[0]);
    }, [leaving, machine, navigation]);

    /* End call works from the first moment the screen offers it, before the
       call itself has begun too. */
    const end = useCallback(() => {
        if (machine.active) {
            machine.end();
            return;
        }
        attemptRef.current += 1;
        session.close();
        setSetup((was) => (was.kind === "opening" || was.kind === "checking" || was.kind === "asking" ? { kind: "cancelled" } : was));
    }, [machine, session]);

    const thinking = view.thinking || session.isRunning;
    const phase = phaseOf(setup, view.status, view.error, view.cause);
    const engineOn = !source.engine && (view.status === "connecting" || view.status === "live");

    return {
        name,
        icon: lemma?.iconUrl ?? null,
        phase,
        words: callWords(phase, name, { muted: view.muted, plan: focused.plan, thinking }, OS),
        muted: view.muted,
        /* The ring's own store (`useSyncExternalStore`), so it alone redraws. */
        level: { subscribe: machine.subscribeLevel, snapshot: machine.levelSnapshot },
        caption: captionOf(view.transcript, name),
        resources: focused.resources,
        conversationId: session.focusedId,
        /* Only for a call that was placed: one refused before it began went nowhere. */
        after: setup.kind === "started" && (phase.kind === "ended" || phase.kind === "failed" || phase.kind === "refused") ? { chat: after, ...afterWords(after) } : null,
        engine: engineOn ? { web: server.web, ref: webRef, onMessage: (message: FromPage) => machine.hear(message) } : null,
        begin,
        end,
        /* Mute reaches the page, so it waits for the call to exist. */
        canMute: view.status === "connecting" || view.status === "live",
        toggleMute: machine.toggleMute,
        openSettings: () => void Linking.openSettings().catch(() => undefined),
        leaving: leaving !== null,
        leave,
        stay: () => setLeaving(null),
    };
}

export type VoiceCall = ReturnType<typeof useVoiceCall>;
