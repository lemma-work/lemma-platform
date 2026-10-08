import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAssistantSession } from "lemma-sdk/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { NEW_CONVERSATION, saidAboutSending, source } from "@/data";
import { initialsOf } from "@/data/agent-names";
import type { ApprovalDecision } from "./approval";
import type { Pod } from "@/data";
import { buildTurns, openInteraction, openSignIn, type RawMessage, type Turn } from "./turns";
import { messageIds, runTouched } from "./run-touched";
import { InteractionDock } from "./interaction-dock";
import type { AnswerWith } from "./interaction-card";
import { isAlreadyUploaded, markAttachment, toAttachments, withReferences, type Attachment } from "./attachments";
import { allConversationsKey, applyTitle, insertConversation, patchConversationLists, refreshConversationLists } from "./conversation-list";
import { Transcript } from "./transcript";
import type { Streaming } from "./turns";
import { Composer } from "./composer";
import { useChannelReply } from "./use-channel-reply";
import { splitQueued, withdrawFailure, withoutSent } from "./queued";
import { sendToConversation, steerConversation } from "./send-message";
import { adoptConversationFolder, useConversationFolder } from "@/desktop/folders";
import { FolderChip } from "@/desktop/folder-chip";
import { needsAiModel, pointsAtModels, runFailure } from "./model-setup";
import { RECONNECTED_EVENT, isTransportFailure } from "@/shell/connection";
import { STUCK_AFTER_MS, TRANSPORT_RELOAD_MS, runLooksStuck } from "./stuck-run";
import { runFailure as describeRunFailure } from "./transcript-state";
import { CLIENT_MESSAGE_KEY, isSending, pendingQueued, pendingSend, stillPending, withPending, type PendingSend } from "./pending-sends";
import { openedTranscripts } from "./opened-transcripts";
import { keepUnchanged } from "./turn-identity";
import { useStableCallback } from "./stable-callback";
import { conversationRefOf } from "@/data/live";

/** The conversation, on the SDK's own session.
 *
 *  Streaming, reattaching to a run that is already going, stop, retry and the
 *  queued-follow-up rule all live in `useAssistantSession` — reimplementing
 *  them here is how the two frontends would start disagreeing about what a
 *  run is. This file only decides what to render and what a click means. */
function stateOf(status?: string): "idle" | "running" | "waiting" | "failed" {
    if (status === "RUNNING" || status === "STOP_REQUESTED") return "running";
    if (status === "WAITING") return "waiting";
    if (status === "FAILED") return "failed";
    return "idle";
}

export function LiveConversation({
    pod,
    conversationId,
    fill,
    onFilled,
    onCreated,
    onOpenApp,
    onOpenFile,
    onOpenTable,
    onVoice,
    callError,
    callRefresh,
    createWith,
    emptyHint,
    placeholder,
    autoSend,
    onAutoSent,
}: {
    pod: Pod;
    conversationId: string | null;
    /** Extra fields for the conversation this pane creates on first send.
     *  A doc's conversation carries its title, standing instructions and the
     *  resource metadata it is found by, and it still only comes into being
     *  when somebody says something. */
    createWith?: Record<string, unknown>;
    /** What an empty conversation says, when the place it is shown in knows
     *  better than the generic "what should it work on". */
    emptyHint?: { title: string; body: string };
    /** What the box says before anything is typed. Defaults to the space's bot. */
    placeholder?: string;
    /** A message handed over from somewhere else — Home, a bot's page — to
     *  send as soon as this pane is up. Sent here rather than there, so the
     *  pane that shows the conversation is the one holding its stream. */
    autoSend?: { text: string; id: number } | null;
    onAutoSent?: () => void;
    /** Text a framed widget or app asked the app to put in the composer.
     *  Arrives as a prop rather than through a ref because opening a new
     *  conversation remounts this component, and the ask has to survive that
     *  to reach the composer it was meant for. */
    fill?: { text: string; id: number } | null;
    onFilled?: () => void;
    onCreated?: (id: string) => void;
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    /** Start a call. Owned by the shell, because a call outlives this
     *  component — it is keyed by conversation, and a call should not end
     *  because someone opened a new one. */
    onVoice?: () => void;
    callError?: string | null;
    callRefresh?: string;
}) {
    const client = useMemo(() => lemma(pod.id), [pod.id]);
    /* Who is answering. A conversation names its bot by `agent_id`, and a new
       one about to be made names it in `createWith`; either way the reply is
       that bot's, not the space's own. */
    const bots = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const queryClient = useQueryClient();
    const [sending, setSending] = useState(false);
    const sendingRef = useRef(false);
    const createdHere = useRef<string | null>(null);
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const [sendError, setSendError] = useState<string | null>(null);
    /* A call that failed to start is the reader's to dismiss; the same text
       coming back later is a new failure and shows again. */
    const [dismissedCallError, setDismissedCallError] = useState<string | null>(null);
    const shownCallError = callError && callError !== dismissedCallError ? callError : null;
    /* The refused send itself, beside its text: its code is what says the
       failure was "no model set up", which the text is not a safe key for. */
    const [sendProblem, setSendProblem] = useState<unknown>(null);

    /* What the teammate last said out loud, captured as it arrives. Reading
       it from a derived selector after the fact did not work: the backend
       marks `is_final_answer` on every assistant message, so "the final
       answer" is not a thing the flags can identify. The last TEXT to land
       is. */
    /* The title the server generated for this conversation, as it arrived.
       Held here as well as patched into the list because the two can race: a
       conversation created a moment ago is titled after its first run, which
       can finish before the list that was invalidated on create has come back
       — and a patch against a list that does not contain it yet is a patch
       that lands nowhere. */
    /* Which conversation the stream belongs to, readable from a callback the
       session owns. A ref rather than the session's own field, which cannot be
       read from inside the options that construct it. */
    const streamingIn = useRef<string | null>(null);

    const historyRequestFailed = useRef(false);
    /* Set below, once the session it reloads exists; read here from the
       session's own error callback. */
    const reloadConversationRef = useRef<() => Promise<void>>(async () => undefined);
    const session = useAssistantSession({
        onError: problem => {
            historyRequestFailed.current = true;
            /* The run's stream failed in transport -- a server restarting under
               it, most often. Re-read the conversation once it is likely back;
               the reconnect strip's recovery event covers a longer outage. */
            if (isTransportFailure(problem)) {
                setTimeout(() => void reloadConversationRef.current(), TRANSPORT_RELOAD_MS);
            }
        },
        client,
        podId: pod.id,
        /* The backend titles a conversation once its first run completes, and
           says so on the conversation's own stream. Before this the name simply
           appeared the next time something refetched the list — usually when
           the person navigated away and back, which reads as the app having
           renamed something behind them. */
        onTitle: (title, id) => {
            const target = id ?? streamingIn.current;
            if (!target) return;
            patchConversationLists(queryClient, pod.id, (list) => applyTitle(list, target, title));
        },
        // Omit agentName: creation uses the pod default when no named agent is supplied.
        conversationId: conversationId === NEW_CONVERSATION ? null : conversationId,
        /* The hook's own bootstrap refreshes the conversation and then loads
           its messages, and something between those two steps was cancelling
           the load — the session ended up holding a status and no messages
           for a conversation the API happily returns 100 for. The load is
           driven from here instead, where the sequence is visible. */
        autoLoad: false,
        autoResume: false,
    });
    /* The folder on this computer the conversation works in — desktop app,
       local install only; `FolderChip` draws nothing anywhere else. */
    const folder = useConversationFolder(session.conversationId ?? null);

    const [historyLoading, setHistoryLoading] = useState(Boolean(conversationId && conversationId !== NEW_CONVERSATION));
    const [loadAttempt, setLoadAttempt] = useState(0);
    const [loadError, setLoadError] = useState<string | null>(null);
    /* The cursor onto everything older than what is on screen. `loadMessages`
       has always returned it and this file has always dropped it, which is why
       a conversation longer than a page simply stopped at its hundredth
       message with nothing saying so. Null means the top is the top. */
    const [olderToken, setOlderToken] = useState<string | null>(null);
    const [loadingOlder, setLoadingOlder] = useState(false);
    /* The scroll hook calls onReachTop on every scroll event under its
       threshold, so the guard has to be a ref: a state flag set in the same
       tick is still false for the next ten of them. */
    const olderInFlight = useRef(false);
    const { loadMessages, refreshConversation, resumeIfRunning } = session;
    const openId = conversationId === NEW_CONVERSATION ? null : conversationId;
    /* Read through a ref, not listed as a dependency: its identity changes
       with `isStreaming`, so every stream starting or ending re-ran the load
       below and re-fetched the conversation and its messages. Declared first
       so it is current by the time the load runs in the same commit. */
    const resumeIfRunningRef = useRef(resumeIfRunning);
    useEffect(() => { resumeIfRunningRef.current = resumeIfRunning; }, [resumeIfRunning]);

    useEffect(() => {
        if (!openId || (createdHere.current === openId && !callRefresh)) return;
        let cancelled = false;
        let historyReady = false;
        historyRequestFailed.current = false;
        setLoadError(null);
        setHistoryLoading(true);
        setOlderToken(null);
        (async () => {
            try {
                /* Side by side: neither needs the other, and one after the
                   other put a whole round trip between opening a conversation
                   and seeing it. */
                const [record, page] = await Promise.all([
                    refreshConversation(openId),
                    loadMessages({ conversationId: openId, limit: 100 }),
                ]);
                if (cancelled) return;
                if (!record || historyRequestFailed.current) throw new Error("History unavailable");
                historyReady = true;
                setOlderToken(page.next_page_token ?? null);
                setHistoryLoading(false);
                if (page.items.length === 0) {
                    setLoadError(null);
                }
                /* Only after the transcript is on screen: reattaching first
                   means a live run writes into a view that has no history. */
                await resumeIfRunningRef.current(openId, { knownConversation: record ?? undefined });
            } catch {
                if (!cancelled && !historyReady) {
                    setLoadError("Could not load this conversation. Please try again.");
                }
            } finally {
                if (!cancelled) setHistoryLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [openId, loadMessages, refreshConversation, callRefresh, loadAttempt]);

    /* Older messages are merged into the session's own list by the controller,
       so there is nothing to stitch here: ask for the next page and the turns
       rebuild with it. The transcript's anchor keeps the reader's line where
       it was while the content grows above them. */
    const loadOlder = useCallback(async () => {
        if (!openId || !olderToken || olderInFlight.current) return false;
        olderInFlight.current = true;
        setLoadingOlder(true);
        try {
            const page = await loadMessages({ conversationId: openId, limit: 100, pageToken: olderToken });
            setOlderToken(page.next_page_token ?? null);
            return page.items.length > 0;
        } catch {
            /* Keep the cursor: the next scroll to the top tries again, which is
               better than a transcript that silently decides it has reached the
               beginning because one request failed. */
            return false;
        } finally {
            olderInFlight.current = false;
            setLoadingOlder(false);
        }
    }, [openId, olderToken, loadMessages]);

    useEffect(() => { streamingIn.current = session.conversationId; }, [session.conversationId]);

    /* What this conversation looked like the last time it was open here, or
       when someone last rested on it in the history: drawn while the server is
       re-read, so going back to a conversation is not a skeleton and a wait.
       Read once per conversation opened; the session's own copy replaces it
       the moment the load lands. */
    const cached = useMemo(() => openedTranscripts.get(pod.id, openId), [pod.id, openId]);
    const fromCache = historyLoading && cached ? cached : null;
    const conversationRecord = session.conversation ?? fromCache?.conversation ?? null;

    const [pending, setPending] = useState<readonly PendingSend[]>([]);
    /* Steers whose request is still in the air. */
    const steering = useRef<Set<string>>(new Set());
    /* A send whose server copy has arrived is the server's to draw. */
    useEffect(() => { setPending(was => stillPending(was, session.messages)); }, [session.messages]);
    /* Drawn as going before the server has said so: the run is what the
       message asks for, and "is working…" belongs on screen with it. */
    const optimistic = isSending(pending);

    const state = stateOf(session.status ?? fromCache?.status);
    const shownState = optimistic && state !== "running" ? "running" : state;
    const agentId = (conversationRecord as { agent_id?: string | null } | null)?.agent_id ?? null;
    const agentName = typeof createWith?.agent_name === "string" ? createWith.agent_name : null;
    const bot = (bots.data ?? []).find((row) => !row.front && ((agentId && row.id === agentId) || (!agentId && agentName && row.name === agentName))) ?? null;
    const teammate = useMemo(
        () => (bot ? { name: bot.label, initials: initialsOf(bot.label), iconUrl: bot.iconUrl } : pod.teammate),
        [bot, pod.teammate],
    );
    const speakerSeed = bot ? pod.id + ":" + bot.name : pod.id;
    /* A conversation that also lives on a chat platform: a note to the bot,
       or a reply the group reads. Read off the conversation as the server
       holds it, so it appears once that has loaded and never on a new one. */
    const reply = useChannelReply(conversationRecord?.metadata, teammate.name, session.conversationId);

    /* After a server restart: the conversation's status, its messages, and --
       if the run is still going -- its stream, read again. Forced, because the
       session's reconnect loop may be asleep in its backoff, and waking it is
       exactly what is wanted now. Failures are left to the next recovery, and
       to the stuck-run offer below. */
    const liveId = session.conversationId ?? openId;
    const reloadConversation = useCallback(async () => {
        if (!liveId || !mounted.current) return;
        try {
            const record = await refreshConversation(liveId);
            await loadMessages({ conversationId: liveId, limit: 100 });
            await resumeIfRunningRef.current(liveId, { knownConversation: record ?? undefined, force: true });
        } catch {
            /* Still unreachable, or the run ended while we looked: either way
               what is on screen is what the server last said. */
        }
    }, [liveId, refreshConversation, loadMessages]);
    useEffect(() => { reloadConversationRef.current = reloadConversation; }, [reloadConversation]);
    useEffect(() => {
        const reconnected = () => void reloadConversation();
        window.addEventListener(RECONNECTED_EVENT, reconnected);
        return () => window.removeEventListener(RECONNECTED_EVENT, reconnected);
    }, [reloadConversation]);

    /* "is working…" with nothing carrying the run: offer to reload rather
       than leave it saying that for ever. */
    const quiet = state === "running" && !session.isStreaming && !historyLoading;
    const [stuck, setStuck] = useState(false);
    useEffect(() => {
        setStuck(false);
        if (!quiet) return;
        const startedAt = Date.now();
        const timer = setTimeout(
            () => setStuck(runLooksStuck("running", false, Date.now() - startedAt)),
            STUCK_AFTER_MS,
        );
        return () => clearTimeout(timer);
    }, [quiet]);
    const reloadStuck = useCallback(() => {
        setStuck(false);
        void reloadConversation();
    }, [reloadConversation]);
    const running = state === "running";
    /* What a run made — a table, a page, an app, a workflow — belongs in the
       space's lists as soon as the run is over, not whenever their cache next
       runs out. A list that stays empty while the chat beside it says "done"
       reads as the work having failed. Once per run, as it stops. */
    const ranBefore = useRef(false);
    const runMessages = useRef(session.messages as readonly RawMessage[]);
    runMessages.current = session.messages as readonly RawMessage[];
    const startedBeside = useRef<ReadonlySet<string>>(new Set());
    /* The server's word only: a cached "running" flipping to the truth on
       load is not a run ending here. */
    const runningLive = stateOf(session.status) === "running";
    useEffect(() => {
        if (runningLive) {
            if (!ranBefore.current) startedBeside.current = messageIds(runMessages.current);
            ranBefore.current = true;
            return;
        }
        if (!ranBefore.current) return;
        ranBefore.current = false;
        /* Only what the run could have changed. An answer, or a run that only
           read, leaves every list as it was — and a refresh of an open table
           is every page of rows it has loaded. */
        const touched = runTouched(runMessages.current, startedBeside.current);
        if (touched === "nothing") return;
        const keys = touched === "files" ? [["library", pod.id]]
            : [["library", pod.id], ["tabs", pod.id], ["workflows", pod.id], ["schedules", pod.id], ["table", pod.id]];
        for (const key of keys) void queryClient.invalidateQueries({ queryKey: key });
    }, [runningLive, pod.id, queryClient]);
    /* A steer the server took while the run was going and whose copy never
       came back on its stream: once the run is over, the transcript the server
       holds is the record, and the tray has nothing left to promise. */
    useEffect(() => {
        if (runningLive) return;
        setPending(was => {
            const left = was.filter(one => one.kind !== "steer" || steering.current.has(one.clientId));
            return left.length === was.length ? was : left;
        });
    }, [runningLive]);
    /* Taken back here, and hidden until the server's list agrees. The session
       has no way to drop a message it holds, and a reload reads the list the
       server has already changed. */
    const [withdrawn, setWithdrawn] = useState<ReadonlySet<string>>(() => new Set());
    const held = fromCache ? fromCache.messages : session.messages;
    const { transcript, queued } = useMemo(() => {
        const split = splitQueued(withPending(held, pending), running, withdrawn);
        const going = pendingQueued(pending, held);
        return going.length === 0 ? split : { ...split, queued: [...split.queued, ...going] };
    }, [held, pending, running, withdrawn]);
    /* Every turn that did not change keeps the object already on screen, so
       its row can skip drawing again when a message lands elsewhere. */
    const shownTurns = useRef<Turn[]>([]);
    const turns = useMemo(() => {
        const next = keepUnchanged(shownTurns.current, buildTurns(transcript));
        shownTurns.current = next;
        return next;
    }, [transcript]);

    /* What the run is blocked on, read straight out of the transcript.
       An approval IS a tool call: `request_approval` streams in like any
       other, and its id is the approval id. The old code fetched the
       approvals list instead, gated on the conversation reaching WAITING —
       which is both a round trip late and sometimes never, because an agent
       host permission wait never leaves RUNNING. The card is in the messages
       the moment the call arrives. */
    const waitingOn = useMemo(() => openInteraction(turns), [turns]);
    const signingIn = useMemo(() => openSignIn(turns), [turns]);
    /* Filled by the docked question while it is open: what is typed answers
       it (see `Questions`). */
    const answerWith = useRef<AnswerWith | null>(null);
    /* The composer is holding more than a few lines; the docked card folds. */
    const [crowded, setCrowded] = useState(false);

    /* Held here rather than in the composer because this is what uploads them,
       clears them on success and leaves them alone on failure — a send that
       did not go through has to leave the files where they were, or the retry
       sends a message that references nothing. */
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const attachmentsRef = useRef<Attachment[]>([]);
    useEffect(() => { attachmentsRef.current = attachments; }, [attachments]);

    const attach = useCallback((files: File[]) => {
        setAttachments(was => [...was, ...toAttachments(files)]);
    }, []);
    const unattach = useCallback((key: string) => {
        setAttachments(was => was.filter(one => one.key !== key));
    }, []);

    /** Put the attached files where the agent will find them, and name them in
     *  the message.
     *
     *  The directory is read off the conversation, never rebuilt: `pod_cwd` is
     *  what the agent's own tools resolve a relative path against, the slug in
     *  it is random, and a second implementation of that rule drifting from the
     *  first is exactly how uploads once landed somewhere the agent never
     *  looked.
     *
     *  This runs after the conversation exists and before the message goes,
     *  which is forced rather than chosen: there is no `pod_cwd` until there is
     *  a conversation, and the references it produces change the message. */
    const putFiles = useCallback(
        async (conversationId: string, content: string, known?: { pod_cwd?: string }) => {
            const pending = attachmentsRef.current;
            if (pending.length === 0) return { content, settled: [] as Attachment[] };

            let directory = known?.pod_cwd;
            if (!directory) {
                const fetched = await client.conversations.get(conversationId, { pod_id: pod.id });
                directory = (fetched as { pod_cwd?: string })?.pod_cwd;
            }
            if (!directory) throw new Error("This conversation has no working directory to attach files to.");

            const landed: { name?: string | null; path: string }[] = [];
            /* What to hand back if the send fails: the same files, marked as
               already in the pod. A retry then references them instead of
               uploading a second copy of each. */
            const settled: Attachment[] = [];
            for (const one of pending) {
                /* Already up there from a send that failed after the upload.
                   Uploading it again would leave two copies of one file, and
                   the second would win the name. */
                if (isAlreadyUploaded(one)) {
                    landed.push({ name: one.file.name, path: one.path });
                    settled.push(one);
                    continue;
                }
                setAttachments(was => markAttachment(was, one.key, { status: "uploading", error: undefined }));
                try {
                    /* One at a time, and `searchEnabled` so the agent can find
                       it by content rather than only by the name in the
                       message. No folder is made first — the upload endpoint
                       creates missing parents on the way. */
                    const written = await client.files.upload(one.file, {
                        name: one.file.name,
                        directoryPath: directory,
                        searchEnabled: true,
                    });
                    setAttachments(was => markAttachment(was, one.key, { status: "uploaded", path: written.path, error: undefined }));
                    landed.push({ name: written.name ?? one.file.name, path: written.path });
                    settled.push({ ...one, status: "uploaded", path: written.path, error: undefined });
                } catch (problem) {
                    setAttachments(was => markAttachment(was, one.key, {
                        status: "failed",
                        error: saidAboutSending(problem, "Upload failed"),
                    }));
                    throw problem;
                }
            }
            return { content: withReferences(content, landed), settled };
        },
        [client, pod.id],
    );

    /** Say something to a run that is already going.
     *
     *  Appended, never streamed: the run already has a stream, and a second
     *  one for the same run duplicates every event on it. The server decides
     *  what "joining" means -- the in-process harness takes it at its next
     *  step, a local coding agent that can be steered hears it within a second
     *  or two, and one that cannot hears it as the next turn -- and says which
     *  in the message's metadata, which is what the tray above the composer
     *  reads. */
    const steer = useCallback(
        async (text: string, id: string) => {
            setSendError(null);
            /* In the tray at once, as "sending…", until the server's copy
               takes its place there or in the transcript. */
            const going = pendingSend(text, "steer", session.messages);
            steering.current.add(going.clientId);
            setPending(was => [...was, going]);
            try {
                await steerConversation(text, id, {
                    putFiles: (conversation, said) => putFiles(conversation, said),
                    /* A note stays a note mid-run too: the mark rides on the
                       message, and the answer to it stays here. */
                    append: (conversation, content) =>
                        client.conversations.appendMessage(
                            conversation,
                            { content, metadata: { ...reply.sendWith, [CLIENT_MESSAGE_KEY]: going.clientId } },
                            { pod_id: pod.id },
                        ),
                    clearAttachments: sent => setAttachments(was => withoutSent(was, sent)),
                    restoreAttachments: settled => setAttachments(was => [
                        ...settled,
                        ...was.filter(one => !settled.some(back => back.key === one.key)),
                    ]),
                    report: message => { if (mounted.current) setSendError(message); },
                });
            } catch (problem) {
                /* The composer puts the words back; the tray lets them go. */
                setPending(was => was.filter(one => one.clientId !== going.clientId));
                throw problem;
            } finally {
                steering.current.delete(going.clientId);
            }
            /* The message arrives on the stream already open for the run. When
               that stream has died, reattaching is what shows it -- forced,
               because a steer never changes the status the dedup key reads. */
            if (!session.isStreaming) {
                void session.resumeIfRunning(id, { expectRun: true, force: true }).catch(() => undefined);
                void loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
            }
        },
        [client, pod.id, putFiles, session, loadMessages, reply.sendWith],
    );

    /* A take-back already on its way. A second click would send a second
       DELETE, whose 409 -- the first one already removed it -- read as the
       teammate having the message. */
    const withdrawing = useRef<Set<string>>(new Set());
    const withdraw = useCallback(
        async (messageId: string) => {
            const id = session.conversationId;
            if (!id || withdrawing.current.has(messageId)) return;
            withdrawing.current.add(messageId);
            setSendError(null);
            try {
                await client.conversations.withdrawMessage(id, messageId, { pod_id: pod.id });
                setWithdrawn(was => new Set([...was, messageId]));
            } catch (problem) {
                /* Usually a race lost to delivery -- the teammate took it in
                   between the tray being drawn and the click -- but only a 409
                   says so. Refetching shows it wherever it now belongs. */
                if (mounted.current) setSendError(withdrawFailure(problem, teammate.name));
                void loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
            } finally {
                withdrawing.current.delete(messageId);
            }
        },
        [client, pod.id, teammate.name, session.conversationId, loadMessages],
    );

    const send = useCallback(
        async (text: string) => {
            if (answerWith.current) return answerWith.current(text);
            const current = createdHere.current ?? session.conversationId;
            if (running && current) return steer(text, current);
            if (sendingRef.current) return;
            sendingRef.current = true;
            setSending(true);
            setSendError(null);
            setSendProblem(null);
            /* On screen now, before the conversation exists or the server has
               heard a word of it. Its id rides along in the metadata, which is
               how the server's copy is known to be this one. */
            const going = pendingSend(text, "send", session.messages);
            setPending(was => [...was, going]);
            try {
                await sendToConversation(text, {
                    conversationId: createdHere.current ?? session.conversationId,
                    /* Created straight off the client, not through the hook.
                       The hook builds its create payload from the same
                       `agentName` the session is scoped by, and this session is
                       scoped by POD_DEFAULT — which the list route understands
                       as a selector and the create route does not: it looks the
                       name up literally, finds no agent called that, and
                       answers 404.

                       Sending the row name instead would create the
                       conversation and then lose it. A conversation with the
                       pod's assistant carries `agent_id` NULL, and the list
                       filters on `COALESCE(agent_id, pod_id) = pod_id`, so
                       naming the default agent explicitly would stamp an
                       agent_id that falls outside the filter it was created
                       for. Omitting the field is the only payload that means
                       "the pod's own assistant". */
                    create: async () => {
                        const made = await client.conversations.create({ pod_id: pod.id, ...createWith } as Parameters<typeof client.conversations.create>[0]);
                        /* A folder chosen while composing is parked in the
                           desktop shell. Adopted here, before the session
                           learns the id and before the first run reads it. */
                        await adoptConversationFolder(made.id, folder.pendingId);
                        return made;
                    },
                    isActive: () => mounted.current,
                    /* Because the conversation is created off the client, the
                       session does not know it exists. Telling the pod first
                       and the session second is the race: the shell re-renders
                       with the new id, the session mirrors that prop, sees an
                       id it has never held, and cancels the stream the send
                       opened in between — the first message dies with "signal
                       is aborted" and the retry, which no longer creates
                       anything, goes through. Handing the session the id here,
                       in the same tick as the create, leaves the pod's later
                       prop with nothing to switch away from. */
                    adopt: made => session.setConversationId(made.id),
                    onCreated: made => {
                        createdHere.current = made.id;
                        onCreated?.(made.id);
                        /* Into the history now, named for what was asked until
                           the server names it — not after a refetch, which
                           would bring it back "Untitled" and only then
                           renamed. The full list is asked for again when the
                           run ends, by which time the server has a title. */
                        insertConversation(queryClient, pod.id, conversationRefOf(made), text);
                        void queryClient.invalidateQueries({ queryKey: allConversationsKey(pod.id) });
                    },
                    send: async (content, id, knownConversation) => {
                        const { content: said, settled } = await putFiles(id, content, knownConversation as { pod_cwd?: string } | undefined);
                        /* Cleared here, before the stream, and that placement is
                           the whole fix. `sendMessage` drains the SSE stream
                           before it resolves, so clearing after it meant the
                           chips sat in the composer for the entire run — the
                           message visibly gone, the agent visibly working, and
                           the files still looking like they were waiting to be
                           sent. By this line they are in the pod and named in
                           the message that is going. */
                        setAttachments(was => withoutSent(was, settled));
                        try {
                            return await session.sendMessage(said, {
                                conversationId: id,
                                knownConversation,
                                metadata: { ...reply.sendWith, [CLIENT_MESSAGE_KEY]: going.clientId },
                            });
                        } catch (problem) {
                            /* Back, but marked as already uploaded: the files
                               are in the pod whatever happened to the message,
                               so a retry references them rather than uploading
                               a second copy of each. Merged rather than
                               assigned, because a run can take minutes and
                               anything attached while it was going is somebody
                               else's work to lose. */
                            setAttachments(was => [
                                ...settled,
                                ...was.filter(one => !settled.some(back => back.key === one.key)),
                            ]);
                            throw problem;
                        }
                    },
                });
                /* The stream has drained, so the server's copy has long since
                   arrived and replaced this one — or never will, and a copy
                   left here would be a message the server does not have. */
                setPending(was => was.filter(one => one.clientId !== going.clientId));
                void refreshConversationLists(queryClient, pod.id);
            } catch (problem) {
                if (mounted.current) {
                    setSendError(saidAboutSending(problem, "That did not send."));
                    setSendProblem(problem);
                    /* Kept where it was written, marked as not sent, with Retry
                       and Edit under it. Not thrown back to the composer: the
                       message is on screen, and the same words in the box as
                       well would be two of it. */
                    setPending(was => was.map(one => one.clientId === going.clientId ? { ...one, state: "failed" } : one));
                }
            } finally {
                sendingRef.current = false;
                if (mounted.current) setSending(false);
            }
        },
        [conversationId, session, client, pod.id, onCreated, queryClient, putFiles, folder.pendingId, running, steer, createWith, reply.sendWith],
    );

    /* A refused message, sent again as a new one, or taken back into the box
       to be changed first. */
    const [refill, setRefill] = useState<{ text: string; id: number } | null>(null);
    const takeBack = useCallback((clientId: string): PendingSend | undefined => {
        const failed = pending.find(one => one.clientId === clientId && one.state === "failed");
        if (failed) setPending(was => was.filter(one => one.clientId !== clientId));
        return failed;
    }, [pending]);
    const retrySend = useStableCallback((clientId: string) => {
        const failed = takeBack(clientId);
        if (failed) void send(failed.text);
    });
    const editSend = useStableCallback((clientId: string) => {
        const failed = takeBack(clientId);
        if (failed) setRefill({ text: failed.text, id: Date.now() });
    });

    /* A handed-over message goes once, the first time this pane sees it. The
       hand-over is cleared first, so a remount cannot send it twice. */
    const autoSentId = useRef<number | null>(null);
    useEffect(() => {
        if (!autoSend || autoSentId.current === autoSend.id) return;
        autoSentId.current = autoSend.id;
        onAutoSent?.();
        send(autoSend.text).catch(() => {
            /* Said on screen by the send itself. */
        });
    }, [autoSend, send, onAutoSent]);

    const resolve = useCallback(
        async (approvalId: string, decision: ApprovalDecision, response?: Record<string, unknown>) => {
            if (!session.conversationId) throw new Error("This conversation is not open yet.");
            const conversation = session.conversationId;
            /* `approvalId` is the tool call id. The card does not clear itself
               here: it holds a submitted state until the tool return lands,
               because an approved tool may take minutes and a card that
               vanishes on click looks like the click was lost. */
            const resolution = (await client.conversations.approvals.resolve(
                conversation,
                approvalId,
                { decision, response: response ?? {} },
                { pod_id: pod.id },
            )) as { status?: string } | undefined;

            /* "queued" means a worker owns everything after the decision,
               including running the approved tool — so the tool return
               provably does not exist yet and reading the transcript would
               only cost a round trip. Anything else finished inline, and the
               return is already there for the card to find. */
            const queued = resolution?.status === "queued";
            /* Answered, so no longer owed: the rail and Home drop it now
               rather than at the next minute's refetch. */
            void queryClient.invalidateQueries({ queryKey: ["conversation-asks"] });
            if (!queued) {
                void loadMessages({ conversationId: conversation, limit: 100 }).catch(() => undefined);
            }
            /* Forced: an agent host permission wait never leaves RUNNING, so
               the ordinary dedup key cannot tell a live subscription from a
               dead one. Right after an explicit decision, reconnecting is
               always warranted. */
            void session
                .resumeIfRunning(conversation, { expectRun: queued ? "queued" : true, force: true })
                .catch(() => undefined);
        },
        [session, client, pod.id, loadMessages, queryClient],
    );

    /* ── the call layer ───────────────────────────────────────────────
       Voice is a transport onto the same turn, never a second brain: the
       small model holds the floor and hands real work to the teammate, so
       permissions, RLS and metering stay exactly where they were. */
    const streaming: Streaming | null = session.isStreaming
        ? {
              text: session.streamingText,
              thinking: session.streamingThinking,
              /* Arguments too, not just the name: the `comment` inside them
                 is what lets the closed row say what the step in flight is
                 for. Dropped here, the row had nothing current to show and
                 fell back to a sentence from several steps ago. */
              tool: session.streamingTool
                  ? {
                        toolName: session.streamingTool.toolName,
                        toolCallId: session.streamingTool.toolCallId,
                        args: session.streamingTool.args,
                    }
                  : null,
          }
        : null;

    /* Read against the state on screen: a message just sent is a new run,
       and the last one's failure is not what it is doing. */
    const failure = runFailure(shownState, optimistic ? null : session.error, conversationRecord);
    const stuckMessage = stuck ? "This run stopped updating. The server may have restarted." : null;
    /* A coding agent's own failure stays readable after a reload too: the
       transcript words it through `transcript-state`, never raw. */
    const recorded = shownState === "failed" && !session.error ? conversationRecord?.last_run_error ?? null : null;
    const agentFailure = recorded && describeRunFailure(recorded).codingAgents ? recorded : null;
    const error = sendError ?? loadError ?? failure.message ?? agentFailure ?? stuckMessage;
    /* Whichever failure is on screen, read by its code rather than its
       words: the words differ by deployment. */
    const modelMissing = sendError ? needsAiModel(sendProblem) : !loadError && failure.noModel;

    /* Handed down stable, so the turns and the composer — memoised — are
       not drawn again for every token of the reply. */
    const retryLoad = useStableCallback(() => setLoadAttempt(attempt => attempt + 1));
    const retryRun = useStableCallback(() => void session.retryFailedRun());
    const resolveStable = useStableCallback(resolve);
    const withdrawStable = useStableCallback((id: string) => void withdraw(id));
    const sendStable = useStableCallback(send);
    const dismissCallError = useStableCallback(() => setDismissedCallError(shownCallError));
    const filled = useStableCallback(() => {
        if (refill) setRefill(null);
        else onFilled?.();
    });

    /* Stop is answered at once: the button goes and the box says so, while
       the request goes out. A run takes a moment to wind down whatever it is
       in the middle of, and a Stop that looked ignored for that moment was
       pressed again. Given back if the request fails. */
    const [stopping, setStopping] = useState(false);
    useEffect(() => { if (!runningLive) setStopping(false); }, [runningLive]);
    const stop = useStableCallback(() => {
        if (stopping) return;
        setStopping(true);
        session.stop().catch(() => {
            if (!mounted.current) return;
            setStopping(false);
            setSendError("Could not stop " + teammate.name + ". Try again.");
        });
    });
    const winding = stopping || session.status === "STOP_REQUESTED";

    /* What this pane last held, for the next time this conversation opens.
       Not while the copy on screen is the cache itself, and not a load that
       failed. */
    useEffect(() => {
        const id = session.conversationId;
        if (!id || historyLoading || loadError || session.messages.length === 0) return;
        openedTranscripts.save(pod.id, id, {
            conversation: session.conversation,
            status: session.status,
            messages: session.messages,
            olderToken,
        });
    }, [pod.id, session.conversationId, session.conversation, session.status, session.messages, olderToken, historyLoading, loadError]);

    /* Unsent words, kept per conversation. A new conversation keeps one only
       in the main pane: the panes beside a doc or a table each start one of
       their own, and one shared "new" draft would follow somebody between
       them. */
    const draftKey = session.conversationId
        ? pod.id + ":" + session.conversationId
        : createWith ? null : pod.id + ":new";

    return (
        <>
            <Transcript
                turns={turns}
                teammate={teammate}
                speakerSeed={speakerSeed}
                streaming={streaming}
                state={shownState}
                error={error}
                loading={historyLoading}
                onReload={loadError ? retryLoad : stuck && error === stuckMessage ? reloadStuck : undefined}
                reloadLabel={loadError ? "Retry" : "Reload conversation"}
                emptyTitle={emptyHint?.title ?? (
                    conversationId === NEW_CONVERSATION || !session.conversationId
                        ? "What should " + teammate.name + " work on?"
                        : "This conversation is empty")
                }
                emptyBody={emptyHint?.body ?? (
                    conversationId === NEW_CONVERSATION || !session.conversationId
                        ? "Send a message to start a new conversation."
                        : "Send a message to start the conversation.")
                }
                podId={pod.id}
                conversationId={session.conversationId}
                hasMore={Boolean(olderToken ?? fromCache?.olderToken)}
                loadingEarlier={loadingOlder}
                onEarlier={loadOlder}
                onOpenApp={onOpenApp}
                onOpenFile={onOpenFile}
                onOpenTable={onOpenTable}
                onResolve={resolveStable}
                onRetry={failure.retryable && !modelMissing ? retryRun : undefined}
                noModel={modelMissing}
                modelsAction={pointsAtModels(error)}
                dockedId={waitingOn?.id ?? signingIn?.id}
                outsiders={reply.thread?.outsiders ? pod.name : undefined}
                onRetrySend={retrySend}
                onEditSend={editSend}
            />
            <InteractionDock
                interaction={waitingOn}
                teammate={teammate.name}
                onResolve={resolveStable}
                /* Not while the status is still unknown, which reads as idle
                   for a moment after load and would flash "Expired". */
                runEnded={session.status !== undefined && state !== "running"}
                signIn={signingIn}
                conversationId={session.conversationId}
                crowded={crowded}
                answerWith={answerWith}
            />
            <FolderChip folder={folder} />
            <Composer
                placeholder={waitingOn?.kind === "question"
                    ? "Or answer in your own words…"
                    : reply.placeholder ?? placeholder ?? "Ask " + (teammate.name || pod.name) + "…"}
                choices={
                    /* What is typed while a question is docked answers it and
                       goes nowhere else, so where it goes is not asked. */
                    waitingOn?.kind === "question" ? undefined : reply.choices
                }
                note={
                    /* Nothing, when the pause is on the shelf directly above
                       this line — a sign-in included, which now sits there
                       too. The note existed to point at a card somewhere up
                       the transcript; with the card here it would be a caption
                       on the thing it is sitting under. Except for what typing
                       does to an approval: the box is not blocked, and a
                       message sent past a request is heard as declining it. */
                    winding && running
                        ? "stopping…"
                        : waitingOn?.kind === "approval"
                        ? "sending a message skips this request"
                        : waitingOn || signingIn
                          ? undefined
                          : state === "waiting"
                            ? "waiting on you"
                            : /* Below the run's own notes: a call that did
                                 not start is worth saying, but not in place of
                                 "waiting on you", which is what the reader
                                 has to act on. */
                              shownCallError ?? (pod.waiting || undefined)
                }
                onDismissNote={
                    shownCallError && !waitingOn && !signingIn && state !== "waiting" && !(winding && running)
                        ? dismissCallError
                        : undefined
                }
                /* `sending` lasts as long as the stream this pane opened, which
                   is the whole run -- so it only holds the box before the run
                   is visibly going. After that, sending again is steering. */
                busy={(sending && !running) || historyLoading || Boolean(loadError)}
                canStop={running && !winding}
                queued={queued}
                queuedNote={
                    queued.length === 0
                        ? undefined
                        : teammate.name + " hears " + (queued.length === 1 ? "this" : "these") + " as soon as the work in progress allows"
                }
                onWithdraw={withdrawStable}
                fill={refill ?? fill}
                onFilled={filled}
                attachments={attachments}
                onAttach={attach}
                onRemoveAttachment={unattach}
                onSend={sendStable}
                onTall={setCrowded}
                onStop={stop}
                onVoice={onVoice}
                draftKey={draftKey}
            />
        </>
    );
}
