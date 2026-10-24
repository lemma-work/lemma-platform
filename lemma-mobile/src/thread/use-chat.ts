import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { AppState } from "react-native";
import { useAssistantSession } from "lemma-sdk/react";
import { useQueryClient } from "@tanstack/react-query";
import { useChatSource } from "../areas/chat/hooks";
import { sampleChatOf, type SampleChat, type SampleRun } from "../areas/chat/sample-chats";
import type { PickedFile } from "../data/io";
import { NEW_CHAT } from "../data/routes";
import { sampleTranscript } from "../data/sample";
import { record, words } from "../data/wire";
import { isRunning } from "@web/live/conversation-stream";
import { pollWhenDown, useLiveStatus, useLiveTopic } from "../live/pod-live";
import { withSocketConversations } from "../live/socket-conversations";
import { lemma } from "../session/client";
import { useSession } from "../session/session";
import type { ApprovalDecision } from "@web/thread/approval";
import { canSend, contentFor, isAlreadyUploaded, markAttachment, toAttachments, uploadFailure, withReferences, type Attachment } from "./attachments";
import { channelThread, rememberReplyMode, rememberedReplyMode, replyChoices, replyModeFor, sendMetadata, type ChannelThread, type ReplyMode } from "@web/thread/channel-reply";
import { composerNote, queuedNote, stateOf, threadOf, type RunState } from "./derive";
import { kept } from "./kept";
import { needsAiModel, pointsAtModels, runFailure } from "@web/thread/model-setup";
import { CLIENT_MESSAGE_KEY, isSending, pendingSend, stillPending, type PendingSend } from "@web/thread/pending-sends";
import { withdrawFailure, withoutSent, type Queued } from "@web/thread/queued";
import type { Choice } from "./runtimes";
import { sendToConversation, steerConversation } from "@web/thread/send-message";
import { stepTimes, type StepTime } from "./steps";
import { STUCK_AFTER_MS, TRANSPORT_RELOAD_MS, runLooksStuck } from "@web/thread/stuck-run";
import { runFailure as describeRunFailure } from "@web/thread/transcript-state";
import { isTransportFailure, saidAboutSending } from "./trouble";
import type { Interaction, OpenSignIn, RawMessage, RuntimeNote, Streaming, Turn } from "@web/thread/turns";

/** One chat, as the screen needs it: the web's `LiveConversation`, without
 *  the drawing.
 *
 *  Live, the conversation is the SDK's own session, `useAssistantSession`,
 *  the hook the web runs: streaming, reattaching to a run already going,
 *  stop, retry and the follow-up rules live there, and a second copy of them
 *  here is how the two apps would start disagreeing about what a run is. Its
 *  stream comes over the Lemma's live socket (`withSocketConversations`),
 *  and over SSE when the socket cannot carry it. A chat left open hears its
 *  conversation's `conversation` topic too, so a run another device started,
 *  or an answer given from Home, lands here; it reads over REST every 15
 *  seconds only while the socket is down.
 *
 *  Sample data runs the same rules over a sample engine, so every state the
 *  thread can be in is there to look at without a server.
 *
 *  A chat opened as `new` has no conversation until the first send creates
 *  one (`onCreated` then gives the route its id); opening a new chat and
 *  leaving makes nothing. */

export type Resolve = (id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => Promise<void>;

/** Answers the open question with a sentence typed into the composer. Throws
 *  when it did not go through, so the composer gives the sentence back. */
export type AnswerWith = (text: string) => Promise<void>;

export interface ChannelReply {
    thread: ChannelThread | null;
    mode: ReplyMode;
    choices: { mode: ReplyMode; label: string; hint: string }[];
    choose: (mode: ReplyMode) => void;
}

export interface Chat {
    conversationId: string | null;
    title: string | null;
    agentId: string | null;
    turns: Turn[];
    queued: Queued[];
    queuedNote: string | undefined;
    /** The run in flight, drawn into the last turn. */
    streaming: Streaming | null;
    state: RunState;
    /** The run that asked is over: a coding agent's request has lapsed. */
    runEnded: boolean;
    /** Answering resumes a new run (the conversation waits on its pauses). */
    resumesOnAnswer: boolean;
    loading: boolean;
    error: string | null;
    noModel: boolean;
    modelsAction: boolean;
    onRetry: (() => void) | undefined;
    onReload: (() => void) | undefined;
    reloadLabel: string;
    hasMore: boolean;
    loadingOlder: boolean;
    loadOlder: () => Promise<boolean>;
    waitingOn: Interaction | null;
    signingIn: OpenSignIn | null;
    running: boolean;
    canStop: boolean;
    busy: boolean;
    note: string | undefined;
    attachments: Attachment[];
    attach: (files: PickedFile[]) => void;
    unattach: (key: string) => void;
    reply: ChannelReply;
    send: (text: string) => Promise<void>;
    resolve: Resolve;
    stop: () => void;
    withdraw: (messageId: string) => void;
    retrySend: (clientId: string) => void;
    editSend: (clientId: string) => void;
    /** Words a refused send put back into the box, to edit before sending. */
    refill: { text: string; id: number } | null;
    refilled: () => void;
    answerWith: MutableRefObject<AnswerWith | null>;
    /** A model chosen before the chat exists; it goes with the create. */
    pickedModel: Choice | null;
    setPickedModel: (choice: Choice | null) => void;
    rename: (title: string | null) => Promise<void>;
    archive: () => Promise<void>;
    /** When a step of a run started and ended, by its call id. */
    timeOf: (callId: string | undefined) => StepTime | undefined;
}

/** The notice a run sends while it waits for a free slot on the person's
 *  computer (`WAITING_FOR_SLOT_STATUS` in the backend's `event_text.py`). */
const WAITING_FOR_SLOT = "agent_host.waiting_for_slot";
const POLL_MS = 15_000;
const PAGE = 100;

/* ── engines: where the messages come from ──────────────────────────────── */

interface Engine {
    conversationId: string | null;
    /** The conversation as the server holds it: title, agent, metadata, the
     *  failure a run left. */
    conversation: Record<string, unknown> | null;
    status: string | undefined;
    messages: RawMessage[];
    streaming: Streaming | null;
    isStreaming: boolean;
    error: Error | null;
    title: string | null;
    notes: RuntimeNote[];
    loading: boolean;
    loadError: string | null;
    hasMore: boolean;
    loadingOlder: boolean;
    loadOlder: () => Promise<boolean>;
    retryLoad: () => void;
    reload: () => Promise<void>;
    markCreated: (id: string) => void;
    create: (choice: Choice | null) => Promise<{ id: string; pod_cwd?: string }>;
    adopt: (id: string) => void;
    send: (content: string, id: string, known: { pod_cwd?: string } | undefined, metadata: Record<string, unknown>) => Promise<void>;
    append: (id: string, content: string, metadata: Record<string, unknown>) => Promise<void>;
    afterSteer: (id: string) => void;
    resolve: (conversation: string, id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => Promise<void>;
    stop: () => Promise<void>;
    withdraw: (conversation: string, messageId: string) => Promise<void>;
    retryRun: () => void;
    setTitle: (title: string | null) => void;
}

function useLiveEngine(podId: string, routeId: string, enabled: boolean, lastTurnId: MutableRefObject<string>): Engine {
    const { server } = useSession();
    const cache = useQueryClient();
    /* The run streams over the Lemma's live socket, shared with every other
       live view of it, and over SSE when the socket cannot carry it. */
    const client = useMemo(() => withSocketConversations(lemma(server, podId), podId), [server, podId]);
    const openId = enabled && routeId !== NEW_CHAT ? routeId : null;

    const [notes, setNotes] = useState<(RuntimeNote & { conversationId: string | null })[]>([]);
    const [slotWait, setSlotWait] = useState<string | null>(null);
    const [title, setTitle] = useState<string | null>(null);
    /* Which conversation the stream belongs to, readable from callbacks the
       session owns. */
    const streamingIn = useRef<string | null>(null);
    const historyRequestFailed = useRef(false);
    const reloadRef = useRef<() => Promise<void>>(async () => undefined);
    const createdHere = useRef<string | null>(null);

    const session = useAssistantSession({
        client,
        podId,
        conversationId: openId,
        /* Driven from here, where the order is visible (the web learned
           this): read the conversation and its messages side by side, draw,
           and only then reattach to a run that is going. */
        autoLoad: false,
        autoResume: false,
        onError: (problem) => {
            historyRequestFailed.current = true;
            /* A stream lost in transport -- a server restarting under it --
               is read again once it is likely back. */
            if (isTransportFailure(problem)) setTimeout(() => void reloadRef.current(), TRANSPORT_RELOAD_MS);
        },
        onTitle: (named, id) => {
            const target = id ?? streamingIn.current;
            if (!target || target === streamingIn.current) setTitle(named);
            void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
        },
        onNotice: (text, kind) => {
            if (kind === WAITING_FOR_SLOT) {
                setSlotWait(text);
                return;
            }
            setNotes((was) => [...was, { text, turnId: lastTurnId.current, conversationId: streamingIn.current }]);
        },
    });
    useEffect(() => { streamingIn.current = session.conversationId; }, [session.conversationId]);

    const [loading, setLoading] = useState(Boolean(openId));
    const [loadAttempt, setLoadAttempt] = useState(0);
    const [loadError, setLoadError] = useState<string | null>(null);
    /* The cursor onto everything older than what is on screen. Null means
       the top is the top. */
    const [olderToken, setOlderToken] = useState<string | null>(null);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const olderInFlight = useRef(false);
    const { loadMessages, refreshConversation, resumeIfRunning } = session;
    /* Through a ref: its identity changes with `isStreaming`, and every
       stream starting or ending would otherwise read the whole chat again. */
    const resumeRef = useRef(resumeIfRunning);
    useEffect(() => { resumeRef.current = resumeIfRunning; }, [resumeIfRunning]);

    useEffect(() => {
        if (!openId || createdHere.current === openId) return;
        let cancelled = false;
        let ready = false;
        historyRequestFailed.current = false;
        setLoadError(null);
        setLoading(true);
        setOlderToken(null);
        void (async () => {
            try {
                const [held, page] = await Promise.all([
                    refreshConversation(openId),
                    loadMessages({ conversationId: openId, limit: PAGE }),
                ]);
                if (cancelled) return;
                if (!held || historyRequestFailed.current) throw new Error("History unavailable");
                ready = true;
                setOlderToken(page.next_page_token ?? null);
                setLoading(false);
                await resumeRef.current(openId, { knownConversation: held });
            } catch {
                if (!cancelled && !ready) setLoadError("Could not load this chat. Try again.");
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [openId, loadMessages, refreshConversation, loadAttempt]);

    const loadOlder = useCallback(async () => {
        const id = session.conversationId ?? openId;
        if (!id || !olderToken || olderInFlight.current) return false;
        olderInFlight.current = true;
        setLoadingOlder(true);
        try {
            const page = await loadMessages({ conversationId: id, limit: PAGE, pageToken: olderToken });
            setOlderToken(page.next_page_token ?? null);
            return page.items.length > 0;
        } catch {
            /* Keep the cursor: the next try asks again, rather than deciding
               the top is reached because one request failed. */
            return false;
        } finally {
            olderInFlight.current = false;
            setLoadingOlder(false);
        }
    }, [session.conversationId, openId, olderToken, loadMessages]);

    /* Gone the moment the run shows any sign of having started, or ends. */
    const underway = !session.isStreaming || Boolean(session.streamingText || session.streamingThinking || session.streamingTool);
    useEffect(() => { setSlotWait(null); }, [underway]);

    /* Read the conversation again: its status, its messages, and -- if a run
       is going -- its stream, forced, since the session's own reconnect may
       be asleep in its backoff. */
    const liveId = session.conversationId ?? openId;
    const reload = useCallback(async () => {
        if (!liveId) return;
        try {
            const held = await refreshConversation(liveId);
            await loadMessages({ conversationId: liveId, limit: PAGE });
            await resumeRef.current(liveId, { knownConversation: held ?? undefined, force: true });
        } catch {
            /* What is on screen is what the server last said. */
        }
    }, [liveId, refreshConversation, loadMessages]);
    useEffect(() => { reloadRef.current = reload; }, [reload]);

    /* Back in front after the phone slept: the socket wakes on its own, and
       what happened meanwhile is read again. */
    useEffect(() => {
        if (!enabled) return;
        const listening = AppState.addEventListener("change", (state) => {
            if (state === "active") void reloadRef.current();
        });
        return () => listening.remove();
    }, [enabled]);

    /* The conversation's own topic, held while the chat is open. While the
       session streams it carries that run itself; otherwise a run starting
       elsewhere, a message landing or a run ending is the cue to read again
       and attach. */
    const sessionRef = useRef(session);
    sessionRef.current = session;
    const nudge = useRef<ReturnType<typeof setTimeout> | null>(null);
    const catchUp = useCallback((started: boolean) => {
        if (nudge.current) clearTimeout(nudge.current);
        nudge.current = setTimeout(() => {
            nudge.current = null;
            const now = sessionRef.current;
            const id = now.conversationId;
            if (!id || now.isStreaming) return;
            void now.loadMessages({ conversationId: id, limit: PAGE })
                .then(() => resumeRef.current(id, started ? { force: true } : undefined))
                .catch(() => undefined);
        }, 300);
    }, []);
    useEffect(() => () => { if (nudge.current) clearTimeout(nudge.current); }, []);
    useLiveTopic(enabled ? podId : null, "conversation", enabled && liveId ? { conversation_id: liveId } : null, {
        onEvents: (events) => {
            for (const raw of events) {
                const event = record(raw);
                const type = String(event.type ?? "");
                if (type === "title") {
                    const named = words(record(event.data).title) ?? words(event.title);
                    if (named) setTitle(named);
                    continue;
                }
                if (sessionRef.current.isStreaming) continue;
                if (type === "run") {
                    if (isRunning(words(event.status))) catchUp(true);
                    continue;
                }
                catchUp(type === "token" || type === "status");
            }
        },
        onResync: () => catchUp(false),
    });
    const socket = useLiveStatus(enabled ? podId : null);
    useEffect(() => {
        if (!enabled) return;
        const every = pollWhenDown(socket, POLL_MS);
        if (every === false) return;
        const timer = setInterval(() => {
            const now = sessionRef.current;
            const id = now.conversationId;
            if (!id || now.isStreaming) return;
            void now.loadMessages({ conversationId: id, limit: PAGE }).then(() => resumeRef.current(id)).catch(() => undefined);
        }, every);
        return () => clearInterval(timer);
    }, [enabled, socket]);

    const liveConversation = session.conversationId ?? null;
    const notesHere = useMemo(() => notes.filter((note) => note.conversationId === liveConversation), [notes, liveConversation]);

    const streaming: Streaming | null = session.isStreaming
        ? {
            text: session.streamingText,
            thinking: session.streamingThinking,
            /* Arguments too: the `comment` in them is what lets the folded
               line say what the step in flight is for. */
            tool: session.streamingTool ? { toolName: session.streamingTool.toolName, toolCallId: session.streamingTool.toolCallId, args: session.streamingTool.args } : null,
            waiting: slotWait,
        }
        : null;

    return {
        conversationId: session.conversationId,
        conversation: (session.conversation as unknown as Record<string, unknown> | null) ?? null,
        status: session.status,
        messages: session.messages as unknown as RawMessage[],
        streaming,
        isStreaming: session.isStreaming,
        error: session.error,
        title,
        notes: notesHere,
        loading,
        loadError,
        hasMore: Boolean(olderToken),
        loadingOlder,
        loadOlder,
        retryLoad: () => setLoadAttempt((attempt) => attempt + 1),
        reload,
        markCreated: (id) => { createdHere.current = id; },
        create: async (choice) => {
            /* Created straight off the client, naming no agent: that is the
               only payload that means "the Lemma's own", and a model chosen
               while this was a draft goes with it so the first run uses it. */
            const made = await client.conversations.create({
                pod_id: podId,
                ...(choice ? { agent_runtime: { profile_id: choice.runtimeId, model_name: choice.model || null } } : {}),
            } as Parameters<typeof client.conversations.create>[0]);
            return made as unknown as { id: string; pod_cwd?: string };
        },
        /* The session hears the id in the same tick as the create, before the
           route does: a session handed an id it has never held cancels the
           stream the send just opened. */
        adopt: (id) => {
            streamingIn.current = id;
            session.setConversationId(id);
        },
        send: async (content, id, known, metadata) => {
            await session.sendMessage(content, { conversationId: id, knownConversation: (known as never) ?? undefined, metadata });
        },
        append: async (id, content, metadata) => {
            await client.conversations.appendMessage(id, { content, metadata }, { pod_id: podId });
        },
        afterSteer: (id) => {
            /* The message arrives on the run's own stream; when that stream
               has died, reattaching is what shows it. */
            if (sessionRef.current.isStreaming) return;
            void resumeRef.current(id, { expectRun: true, force: true }).catch(() => undefined);
            void loadMessages({ conversationId: id, limit: PAGE }).catch(() => undefined);
        },
        resolve: async (conversation, id, decision, response) => {
            const resolution = (await client.conversations.approvals.resolve(conversation, id, { decision, response: response ?? {} } as never, { pod_id: podId } as never)) as { status?: string } | undefined;
            /* "queued": a worker owns everything after the decision, so the
               return does not exist yet. Anything else finished inline. */
            const queued = resolution?.status === "queued";
            void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
            if (!queued) void loadMessages({ conversationId: conversation, limit: PAGE }).catch(() => undefined);
            void resumeRef.current(conversation, { expectRun: queued ? "queued" : true, force: true }).catch(() => undefined);
        },
        stop: () => session.stop(),
        withdraw: async (conversation, messageId) => {
            await client.conversations.withdrawMessage(conversation, messageId, { pod_id: podId });
        },
        retryRun: () => void session.retryFailedRun().catch(() => undefined),
        setTitle,
    };
}

/* ── the sample engine ──────────────────────────────────────────────────── */

const SAMPLE_REPLY = "This is sample data, so nothing was sent. Sign in to ask your own Lemmas.";

/** What a sample Lemma does before it says a sample reply: one step, so the
 *  line under the reply has something to say. */
const SAMPLE_STEPS: SampleRun["steps"] = [
    { tool: "pod_search_files", args: { query: "what was asked", comment: "Looking through the Lemma's files" }, result: { results: [] }, ms: 1300 },
];

function sampleStart(routeId: string): SampleChat {
    const rich = sampleChatOf(routeId, Date.now());
    if (rich) return rich;
    return { title: null, status: "IDLE", messages: routeId === NEW_CHAT ? [] : sampleTranscript(routeId, Date.now()), older: [], conversation: {} };
}

function useSampleEngine(routeId: string, enabled: boolean): Engine {
    const [chat] = useState(() => (enabled ? sampleStart(routeId) : null));
    const [messages, setMessages] = useState<RawMessage[]>(() => chat?.messages ?? []);
    const [older, setOlder] = useState<RawMessage[]>(() => chat?.older ?? []);
    const [status, setStatus] = useState<string | undefined>(chat?.status);
    const [streaming, setStreaming] = useState<Streaming | null>(chat?.streaming ?? null);
    const [error, setError] = useState<Error | null>(null);
    const [title, setTitle] = useState<string | null>(chat?.title ?? null);
    const [conversationId, setConversationId] = useState<string | null>(routeId === NEW_CHAT ? null : routeId);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [conversation, setConversation] = useState<Record<string, unknown>>(() => chat?.conversation ?? {});
    const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
    useEffect(() => () => timers.current.forEach(clearTimeout), []);
    const later = (ms: number) => new Promise<void>((resolve) => { timers.current.push(setTimeout(resolve, ms)); });
    const next = (was: RawMessage[]) => was.reduce((top, one) => Math.max(top, one.sequence ?? 0), 0) + 1;
    const add = (message: RawMessage) => setMessages((was) => [...was, { ...message, sequence: next(was) }]);
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);
    /* A run played out: each step announced while it goes and landed when
       it is done, then the reply a few words at a time, as a stream
       arrives. Anything said meanwhile is heard once it ends. */
    const play = async (run: SampleRun) => {
        setStatus("RUNNING");
        setStreaming({ text: "", thinking: "", tool: null });
        for (const [index, step] of run.steps.entries()) {
            const id = `sample-step-${Date.now()}-${index}`;
            const began = Date.now();
            setStreaming({ text: "", thinking: "", tool: { toolName: step.tool, toolCallId: id, args: step.args } });
            await later(step.ms);
            if (!alive.current) return;
            add({ id: id + "-c", role: "assistant", kind: "TOOL_CALL", tool_name: step.tool, tool_args: step.args, tool_call_id: id, created_at: new Date(began).toISOString() });
            add({ id: id + "-r", role: "tool", kind: "TOOL_RETURN", tool_name: step.tool, tool_call_id: id, tool_result: step.result ?? { success: true }, created_at: new Date().toISOString() });
        }
        const chunks = run.reply.match(/\S+\s*/g) ?? [run.reply];
        let said = "";
        for (let index = 0; index < chunks.length; index += 3) {
            said += chunks.slice(index, index + 3).join("");
            setStreaming({ text: said, thinking: "", tool: null });
            await later(150);
            if (!alive.current) return;
        }
        setStreaming(null);
        add({ id: "a" + Date.now(), role: "assistant", kind: "TEXT", content: run.reply, created_at: new Date().toISOString() });
        setStatus("IDLE");
        setMessages((was) => was.map((one) => (one.metadata?.during_active_run ? { ...one, metadata: { ...one.metadata, during_active_run: false } } : one)));
        if (run.then) await play(run.then);
    };
    const answer = (text: string) => play({ steps: SAMPLE_STEPS, reply: text });
    useEffect(() => {
        if (chat?.run) void play(chat.run);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return {
        conversationId,
        conversation,
        status,
        messages,
        streaming,
        isStreaming: streaming !== null,
        error,
        title,
        notes: chat?.notes ?? [],
        loading: false,
        loadError: null,
        hasMore: older.length > 0,
        loadingOlder,
        loadOlder: async () => {
            if (older.length === 0) return false;
            setLoadingOlder(true);
            await later(500);
            setMessages((was) => [...older, ...was]);
            setOlder([]);
            setLoadingOlder(false);
            return true;
        },
        retryLoad: () => undefined,
        reload: async () => {
            await later(400);
            setStatus("IDLE");
        },
        markCreated: () => undefined,
        create: async () => ({ id: "sample-" + Date.now().toString(36), pod_cwd: "/me/conversations/sample" }),
        adopt: (id) => setConversationId(id),
        send: async (content, _id, _known, metadata) => {
            add({ id: "u" + Date.now(), role: "user", kind: "TEXT", content, created_at: new Date().toISOString(), metadata });
            setError(null);
            await answer(SAMPLE_REPLY);
        },
        append: async (_id, content, metadata) => {
            add({ id: "q" + Date.now(), role: "user", kind: "TEXT", content, created_at: new Date().toISOString(), metadata: { ...metadata, during_active_run: true } });
        },
        afterSteer: () => undefined,
        resolve: async (_conversation, id, decision, response) => {
            await later(400);
            add({ id: "ret-" + id, role: "tool", kind: "TOOL_RETURN", tool_name: "request_approval", tool_call_id: id, tool_result: { decision, ...(response ?? {}) }, created_at: new Date().toISOString() });
            const after = chat?.after?.[id];
            if (after && decision !== "DENY") await answer(after);
            else if (decision === "DENY") await answer("Understood, I won’t do that. Tell me what you would like instead.");
        },
        stop: async () => {
            await later(300);
            setStreaming(null);
            setStatus("IDLE");
            /* Nothing left to hear what was said meanwhile: it becomes a
               message that was sent. */
            setMessages((was) => was.map((one) => (one.metadata?.during_active_run ? { ...one, metadata: { ...one.metadata, during_active_run: false } } : one)));
        },
        withdraw: async (_conversation, messageId) => {
            await later(250);
            setMessages((was) => was.filter((one) => one.id !== messageId));
        },
        retryRun: () => {
            setConversation((was) => ({ ...was, last_run_error: null, last_run_error_code: null }));
            void answer(chat?.retried ?? SAMPLE_REPLY);
        },
        setTitle,
    };
}

/* ── the chat, over either engine ───────────────────────────────────────── */

/** A handler whose identity never changes and which always runs the latest
 *  version: the turns and the composer are memoised, and a handler made
 *  fresh each render would draw them all again for every token of a reply. */
function useStable<A extends unknown[], R>(run: (...args: A) => R): (...args: A) => R {
    const latest = useRef(run);
    latest.current = run;
    return useCallback((...args: A) => latest.current(...args), []);
}

export function useChat(podId: string, routeId: string, name: string, onCreated: (id: string) => void): Chat {
    const { mode } = useSession();
    const lastTurnId = useRef("");
    const live = useLiveEngine(podId, routeId, mode === "live", lastTurnId);
    const sample = useSampleEngine(routeId, mode === "sample");
    return useChatOver(mode === "sample" ? sample : live, podId, name, onCreated, lastTurnId);
}

function useChatOver(engine: Engine, podId: string, name: string, onCreated: (id: string) => void, lastTurnId: MutableRefObject<string>): Chat {
    const source = useChatSource();
    const cache = useQueryClient();
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const created = useRef(onCreated);
    created.current = onCreated;

    const [sending, setSending] = useState(false);
    const sendingRef = useRef(false);
    const createdHere = useRef<string | null>(null);
    const [sendError, setSendError] = useState<string | null>(null);
    /* The refused send itself: its code says "no model set up", which its
       words are not a safe key for. */
    const [sendProblem, setSendProblem] = useState<unknown>(null);
    const [pickedModel, setPickedModel] = useState<Choice | null>(null);
    const uploadFailed = useRef<string | null>(null);
    const [pending, setPending] = useState<readonly PendingSend[]>([]);
    const steering = useRef<Set<string>>(new Set());
    const [withdrawn, setWithdrawn] = useState<ReadonlySet<string>>(() => new Set());
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const attachmentsRef = useRef<Attachment[]>([]);
    useEffect(() => { attachmentsRef.current = attachments; }, [attachments]);

    /* A send whose server copy has arrived is the server's to draw. */
    useEffect(() => { setPending((was) => stillPending(was, engine.messages)); }, [engine.messages]);
    const optimistic = isSending(pending);
    const state = stateOf(engine.status);
    const shownState: RunState = optimistic && state !== "running" ? "running" : state;
    const running = state === "running";
    const conversationId = engine.conversationId;
    const record_ = engine.conversation;

    /* A chat that also lives on a chat platform: a note to the Lemma alone,
       or a reply the people there read. Its own default, then whatever was
       last chosen in it on this phone. */
    const thread = useMemo(() => channelThread(record(record_?.metadata)), [record_]);
    const [made, setMade] = useState<{ in: string | null; mode: ReplyMode } | null>(null);
    const remembered = useMemo(() => (thread ? rememberedReplyMode(kept, conversationId) : null), [thread, conversationId]);
    const replyMode: ReplyMode = made && made.in === conversationId ? made.mode : thread ? replyModeFor(thread, remembered) : "note";
    const sendWith = useMemo(() => sendMetadata(thread, replyMode) ?? {}, [thread, replyMode]);
    const reply: ChannelReply = useMemo(() => ({
        thread,
        mode: replyMode,
        choices: thread ? replyChoices(thread, name) : [],
        choose: (next: ReplyMode) => {
            setMade({ in: conversationId, mode: next });
            rememberReplyMode(kept, conversationId, next);
        },
    }), [thread, replyMode, name, conversationId]);

    /* A steer the server took while the run went and whose copy never came
       back on its stream: once the run is over the transcript is the record. */
    useEffect(() => {
        if (running) return;
        setPending((was) => {
            const left = was.filter((one) => one.kind !== "steer" || steering.current.has(one.clientId));
            return left.length === was.length ? was : left;
        });
    }, [running]);

    /* What a run made belongs in the lists, and in the cards that read
       tables, as soon as it is over. Once per run, as it stops. */
    const ran = useRef(false);
    useEffect(() => {
        if (running) {
            ran.current = true;
            return;
        }
        if (!ran.current) return;
        ran.current = false;
        void cache.invalidateQueries({ predicate: (query) => query.queryKey.some((part) => part === "pulse" || part === "chat-table" || part === "chat-query" || part === "chat-file") });
    }, [running, cache]);

    const shown = useRef<Turn[]>([]);
    const view = useMemo(() => {
        const next = threadOf({ messages: engine.messages, pending, running, withdrawn, notes: engine.notes, previous: shown.current });
        shown.current = next.turns;
        return next;
    }, [engine.messages, pending, running, withdrawn, engine.notes]);
    useEffect(() => { lastTurnId.current = view.turns.at(-1)?.id ?? ""; }, [view.turns, lastTurnId]);

    /* "Working…" with nothing carrying the run: after a while, offer to
       read it again rather than say that for ever. */
    const quiet = state === "running" && !engine.isStreaming && !engine.loading;
    const [stuck, setStuck] = useState(false);
    useEffect(() => {
        setStuck(false);
        if (!quiet) return;
        const startedAt = Date.now();
        const timer = setTimeout(() => setStuck(runLooksStuck("running", false, Date.now() - startedAt)), STUCK_AFTER_MS);
        return () => clearTimeout(timer);
    }, [quiet]);

    /* The ref too, at once: files handed over with a message are attached
       and sent in the same tick, before the render that would sync it, and
       the ref is what `putFiles` reads. */
    const attach = useCallback((files: PickedFile[]) => {
        const made = toAttachments(files);
        attachmentsRef.current = [...attachmentsRef.current, ...made];
        setAttachments((was) => [...was, ...made]);
    }, []);
    const unattach = useCallback((key: string) => setAttachments((was) => was.filter((one) => one.key !== key)), []);

    /** Put the attached files where the agent will find them -- the
     *  conversation's `pod_cwd`, read off it, never rebuilt -- and name them
     *  in the message. After the conversation exists, before the message. */
    const putFiles = useCallback(async (id: string, content: string, known?: { pod_cwd?: string }) => {
        const held = attachmentsRef.current;
        if (held.length === 0) return { content, settled: [] as Attachment[] };
        const directory = known?.pod_cwd || (await source.workingFolder(podId, id));
        if (!directory) throw new Error("This chat has no folder to attach files to.");
        const landed: { name?: string | null; path: string }[] = [];
        const settled: Attachment[] = [];
        for (const one of held) {
            if (isAlreadyUploaded(one)) {
                landed.push({ name: one.file.name, path: one.path });
                settled.push(one);
                continue;
            }
            setAttachments((was) => markAttachment(was, one.key, { status: "uploading", error: undefined }));
            try {
                const written = await source.upload(podId, one.file as PickedFile, directory);
                setAttachments((was) => markAttachment(was, one.key, { status: "uploaded", path: written.path, error: undefined }));
                landed.push({ name: written.name, path: written.path });
                settled.push({ ...one, status: "uploaded", path: written.path, error: undefined });
            } catch (problem) {
                const sentence = uploadFailure(one.file.name, saidAboutSending(problem, ""));
                uploadFailed.current = sentence;
                setAttachments((was) => markAttachment(was, one.key, { status: "failed", error: sentence }));
                throw problem;
            }
        }
        return { content: withReferences(content, landed), settled };
    }, [source, podId]);

    const restore = (settled: Attachment[]) => setAttachments((was) => [...settled, ...was.filter((one) => !settled.some((back) => back.key === one.key))]);

    /** Say something to a run already going: appended, never streamed, since
     *  the run has a stream and a second would repeat every event on it. */
    const steer = useCallback(async (text: string, id: string) => {
        setSendError(null);
        uploadFailed.current = null;
        const going = pendingSend(text, "steer", engine.messages);
        steering.current.add(going.clientId);
        setPending((was) => [...was, going]);
        try {
            await steerConversation(text, id, {
                putFiles: (conversation, said) => putFiles(conversation, said),
                append: (conversation, content) => engine.append(conversation, content, { ...sendWith, [CLIENT_MESSAGE_KEY]: going.clientId }),
                clearAttachments: (sent) => setAttachments((was) => withoutSent(was, sent)),
                restoreAttachments: restore,
                report: (message) => {
                    if (mounted.current) setSendError(uploadFailed.current ?? message);
                    uploadFailed.current = null;
                },
            });
        } catch (problem) {
            setPending((was) => was.filter((one) => one.clientId !== going.clientId));
            throw problem;
        } finally {
            steering.current.delete(going.clientId);
        }
        engine.afterSteer(id);
    }, [engine, putFiles, sendWith]);

    /* Filled by the docked question while it is open: what is typed answers
       it (see `interaction.tsx`). */
    const answerWith = useRef<AnswerWith | null>(null);

    const send = useCallback(async (typed: string) => {
        const text = contentFor(typed, attachmentsRef.current);
        if (!canSend(text, attachmentsRef.current)) return;
        if (answerWith.current) return answerWith.current(text);
        const current = createdHere.current ?? conversationId;
        if (running && current) return steer(text, current);
        if (sendingRef.current) return;
        sendingRef.current = true;
        setSending(true);
        setSendError(null);
        setSendProblem(null);
        uploadFailed.current = null;
        /* On screen now, before the conversation exists; its id rides in the
           metadata, which is how the server's copy is known to be this one. */
        const going = pendingSend(text, "send", engine.messages);
        setPending((was) => [...was, going]);
        try {
            await sendToConversation(text, {
                conversationId: current,
                create: () => engine.create(pickedModel),
                isActive: () => mounted.current,
                adopt: (conversation) => engine.adopt(conversation.id),
                onCreated: (conversation) => {
                    createdHere.current = conversation.id;
                    engine.markCreated(conversation.id);
                    created.current(conversation.id);
                    void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
                },
                send: async (content, id, known) => {
                    const { content: said, settled } = await putFiles(id, content, known);
                    /* Out of the composer before the stream, which lasts the
                       whole run: by now they are in the pod and named. */
                    setAttachments((was) => withoutSent(was, settled));
                    try {
                        await engine.send(said, id, known, { ...sendWith, [CLIENT_MESSAGE_KEY]: going.clientId });
                    } catch (problem) {
                        /* Back, marked as uploaded: a retry names them
                           rather than uploading each again. */
                        restore(settled);
                        throw problem;
                    }
                },
            });
            setPending((was) => was.filter((one) => one.clientId !== going.clientId));
            void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
        } catch (problem) {
            if (mounted.current) {
                setSendError(uploadFailed.current ?? saidAboutSending(problem, "That did not send."));
                uploadFailed.current = null;
                setSendProblem(problem);
                /* Kept where it was written, marked as not sent, with Retry
                   and Edit under it -- not put back in the box, where the same
                   words twice would be two of it. */
                setPending((was) => was.map((one) => (one.clientId === going.clientId ? { ...one, state: "failed" } : one)));
            }
        } finally {
            sendingRef.current = false;
            if (mounted.current) setSending(false);
        }
    }, [conversationId, running, steer, engine, pickedModel, putFiles, sendWith, cache]);

    const [refill, setRefill] = useState<{ text: string; id: number } | null>(null);
    const takeBack = (clientId: string): PendingSend | undefined => {
        const failed = pending.find((one) => one.clientId === clientId && one.state === "failed");
        if (failed) setPending((was) => was.filter((one) => one.clientId !== clientId));
        return failed;
    };
    const retrySend = useStable((clientId: string) => {
        const failed = takeBack(clientId);
        if (failed) void send(failed.text);
    });
    const editSend = useStable((clientId: string) => {
        const failed = takeBack(clientId);
        if (failed) setRefill({ text: failed.text, id: Date.now() });
    });

    /* A take-back already on its way: a second tap would send a second
       DELETE, whose 409 would read as the Lemma having the message. */
    const withdrawing = useRef<Set<string>>(new Set());
    const withdraw = useStable((messageId: string) => {
        const id = conversationId;
        if (!id || withdrawing.current.has(messageId)) return;
        withdrawing.current.add(messageId);
        setSendError(null);
        void engine.withdraw(id, messageId)
            .then(() => setWithdrawn((was) => new Set([...was, messageId])))
            .catch((problem: unknown) => {
                if (mounted.current) setSendError(withdrawFailure(problem, name));
                void engine.reload();
            })
            .finally(() => withdrawing.current.delete(messageId));
    });

    const resolve: Resolve = useStable(async (id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => {
        if (!conversationId) throw new Error("This chat is not open yet.");
        await engine.resolve(conversationId, id, decision, response);
    });

    /* Stop is answered at once; given back if the request fails. */
    const [stopping, setStopping] = useState(false);
    useEffect(() => { if (!running) setStopping(false); }, [running]);
    const stop = useStable(() => {
        if (stopping) return;
        setStopping(true);
        engine.stop().catch(() => {
            if (!mounted.current) return;
            setStopping(false);
            setSendError("Could not stop " + name + ". Try again.");
        });
    });
    const winding = stopping || engine.status === "STOP_REQUESTED";

    /* Read against what is on screen: a message just sent is a new run, and
       the last one's failure is not what it is doing. */
    const failure = runFailure(shownState, optimistic ? null : engine.error, record_ as never);
    const stuckMessage = stuck ? "This run stopped updating. The server may have restarted." : null;
    const recorded = shownState === "failed" && !engine.error ? words(record_?.last_run_error) : null;
    const agentFailure = recorded && describeRunFailure(recorded).codingAgents ? recorded : null;
    const error = sendError ?? engine.loadError ?? failure.message ?? agentFailure ?? stuckMessage;
    const noModel = sendError ? needsAiModel(sendProblem) : !engine.loadError && failure.noModel;

    const times = useMemo(() => stepTimes(engine.messages), [engine.messages]);
    const timeOf = useStable((callId: string | undefined) => (callId ? times.get(callId) : undefined));

    const refilled = useCallback(() => setRefill(null), []);
    const sendStable = useStable(send);
    const retryRun = useStable(() => engine.retryRun());
    const reloadStuck = useStable(() => { setStuck(false); void engine.reload(); });
    const retryLoad = useStable(() => engine.retryLoad());
    const rename = async (next: string | null) => {
        if (!conversationId) return;
        const before = engine.title;
        engine.setTitle(next);
        try {
            await source.rename(podId, conversationId, next);
            void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
        } catch (problem) {
            engine.setTitle(before);
            throw problem;
        }
    };
    const archive = async () => {
        if (!conversationId) return;
        await source.archive(podId, conversationId);
        void cache.invalidateQueries({ predicate: (query) => query.queryKey.includes("pulse") });
    };

    return {
        conversationId,
        title: engine.title ?? words(record_?.title),
        agentId: words(record_?.agent_id),
        turns: view.turns,
        queued: view.queued,
        queuedNote: queuedNote(view.queued, name),
        streaming: engine.streaming,
        state: shownState,
        runEnded: engine.status !== undefined && state !== "running",
        resumesOnAnswer: state === "waiting",
        loading: engine.loading,
        error,
        noModel,
        modelsAction: pointsAtModels(error),
        onRetry: failure.retryable && !noModel ? retryRun : undefined,
        onReload: engine.loadError ? retryLoad : stuck && error === stuckMessage ? reloadStuck : undefined,
        reloadLabel: engine.loadError ? "Try again" : "Reload the chat",
        hasMore: engine.hasMore,
        loadingOlder: engine.loadingOlder,
        loadOlder: engine.loadOlder,
        waitingOn: view.waitingOn,
        signingIn: view.signingIn,
        running,
        canStop: running && !winding,
        busy: (sending && !running) || engine.loading || Boolean(engine.loadError),
        note: composerNote({ running, winding, state, waitingOn: view.waitingOn, signingIn: view.signingIn }),
        attachments,
        attach,
        unattach,
        reply,
        send: sendStable,
        resolve,
        stop,
        withdraw,
        retrySend,
        editSend,
        refill,
        refilled,
        answerWith,
        pickedModel,
        setPickedModel,
        rename,
        archive,
        timeOf,
    };
}
