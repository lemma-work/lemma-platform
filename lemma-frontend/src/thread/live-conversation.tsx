import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAssistantSession } from "lemma-sdk/react";
import { useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { NEW_CONVERSATION, saidAboutSending } from "@/data";
import type { ApprovalDecision } from "./approval";
import type { Pod } from "@/data";
import { buildTurns, openInteraction, openSignIn } from "./turns";
import { InteractionDock } from "./interaction-dock";
import { isAlreadyUploaded, markAttachment, toAttachments, withReferences, type Attachment } from "./attachments";
import { applyTitle } from "./conversation-list";
import type { ConversationRef } from "@/data";
import { Transcript } from "./transcript";
import type { Streaming } from "./turns";
import { Composer } from "./composer";
import { splitQueued, withdrawFailure, withoutSent } from "./queued";
import { sendToConversation, steerConversation } from "./send-message";
import { adoptConversationFolder, useConversationFolder } from "@/desktop/folders";
import { FolderChip } from "@/desktop/folder-chip";
import { needsAiModel, pointsAtModels, runFailure } from "./model-setup";
import { RECONNECTED_EVENT, isTransportFailure } from "@/shell/connection";
import { STUCK_AFTER_MS, TRANSPORT_RELOAD_MS, runLooksStuck } from "./stuck-run";

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
}: {
    pod: Pod;
    conversationId: string | null;
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
            queryClient.setQueryData<ConversationRef[]>(
                ["conversations", pod.id],
                previous => applyTitle(previous, target, title),
            );
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
                const record = await refreshConversation(openId);
                if (cancelled) return;
                if (!record || historyRequestFailed.current) throw new Error("History unavailable");
                const page = await loadMessages({ conversationId: openId, limit: 100 });
                if (cancelled) return;
                if (historyRequestFailed.current) throw new Error("History unavailable");
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

    const state = stateOf(session.status);

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
    /* Taken back here, and hidden until the server's list agrees. The session
       has no way to drop a message it holds, and a reload reads the list the
       server has already changed. */
    const [withdrawn, setWithdrawn] = useState<ReadonlySet<string>>(() => new Set());
    const { transcript, queued } = useMemo(
        () => splitQueued(session.messages, running, withdrawn),
        [session.messages, running, withdrawn],
    );
    const turns = useMemo(() => buildTurns(transcript), [transcript]);

    /* What the run is blocked on, read straight out of the transcript.
       An approval IS a tool call: `request_approval` streams in like any
       other, and its id is the approval id. The old code fetched the
       approvals list instead, gated on the conversation reaching WAITING —
       which is both a round trip late and sometimes never, because an agent
       host permission wait never leaves RUNNING. The card is in the messages
       the moment the call arrives. */
    const waitingOn = useMemo(() => openInteraction(turns), [turns]);
    const signingIn = useMemo(() => openSignIn(turns), [turns]);

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
            await steerConversation(text, id, {
                putFiles: (conversation, said) => putFiles(conversation, said),
                append: (conversation, content) =>
                    client.conversations.appendMessage(conversation, { content }, { pod_id: pod.id }),
                clearAttachments: sent => setAttachments(was => withoutSent(was, sent)),
                restoreAttachments: settled => setAttachments(was => [
                    ...settled,
                    ...was.filter(one => !settled.some(back => back.key === one.key)),
                ]),
                report: message => { if (mounted.current) setSendError(message); },
            });
            /* The message arrives on the stream already open for the run. When
               that stream has died, reattaching is what shows it -- forced,
               because a steer never changes the status the dedup key reads. */
            if (!session.isStreaming) {
                void session.resumeIfRunning(id, { expectRun: true, force: true }).catch(() => undefined);
                void loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
            }
        },
        [client, pod.id, putFiles, session, loadMessages],
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
                if (mounted.current) setSendError(withdrawFailure(problem, pod.teammate.name));
                void loadMessages({ conversationId: id, limit: 100 }).catch(() => undefined);
            } finally {
                withdrawing.current.delete(messageId);
            }
        },
        [client, pod.id, pod.teammate.name, session.conversationId, loadMessages],
    );

    const send = useCallback(
        async (text: string) => {
            const current = createdHere.current ?? session.conversationId;
            if (running && current) return steer(text, current);
            if (sendingRef.current) return;
            sendingRef.current = true;
            setSending(true);
            setSendError(null);
            setSendProblem(null);
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
                        const made = await client.conversations.create({ pod_id: pod.id });
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
                        void queryClient.invalidateQueries({ queryKey: ["conversations", pod.id] });
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
                            return await session.sendMessage(said, { conversationId: id, knownConversation });
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
                void queryClient.invalidateQueries({ queryKey: ["conversations", pod.id] });
            } catch (problem) {
                if (mounted.current) {
                    setSendError(saidAboutSending(problem, "That did not send."));
                    setSendProblem(problem);
                }
                throw problem;
            } finally {
                sendingRef.current = false;
                if (mounted.current) setSending(false);
            }
        },
        [conversationId, session, client, pod.id, onCreated, queryClient, putFiles, folder.pendingId, running, steer],
    );

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
        [session, client, pod.id, loadMessages],
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

    const failure = runFailure(state, session.error, session.conversation);
    const stuckMessage = stuck ? "This run stopped updating. The server may have restarted." : null;
    const error = sendError ?? loadError ?? failure.message ?? stuckMessage;
    /* Whichever failure is on screen, read by its code rather than its
       words: the words differ by deployment. */
    const modelMissing = sendError ? needsAiModel(sendProblem) : !loadError && failure.noModel;

    return (
        <>
            <Transcript
                turns={turns}
                teammate={pod.teammate}
                streaming={streaming}
                state={state}
                error={error}
                loading={historyLoading}
                onReload={loadError ? () => setLoadAttempt(attempt => attempt + 1) : stuck && error === stuckMessage ? reloadStuck : undefined}
                reloadLabel={loadError ? "Retry" : "Reload conversation"}
                emptyTitle={
                    conversationId === NEW_CONVERSATION || !session.conversationId
                        ? "New conversation"
                        : "This conversation is empty"
                }
                emptyBody={
                    conversationId === NEW_CONVERSATION || !session.conversationId
                        ? pod.teammate.name + " is ready. Send a message to start."
                        : "Send a message to start the conversation."
                }
                podId={pod.id}
                conversationId={session.conversationId}
                hasMore={Boolean(olderToken)}
                loadingEarlier={loadingOlder}
                onEarlier={loadOlder}
                onOpenApp={onOpenApp}
                onOpenFile={onOpenFile}
                onOpenTable={onOpenTable}
                onResolve={resolve}
                onRetry={failure.retryable && !modelMissing ? () => void session.retryFailedRun() : undefined}
                noModel={modelMissing}
                modelsAction={pointsAtModels(error)}
                dockedId={waitingOn?.id}
            />
            <InteractionDock interaction={waitingOn} teammate={pod.teammate.name} onResolve={resolve} />
            <FolderChip folder={folder} />
            <Composer
                placeholder={"Talk to " + pod.name + "…"}
                note={
                    /* Nothing, when the pause is on the shelf directly above
                       this line. The note existed to point at a card somewhere
                       up the transcript; with the card here it would be a
                       caption on the thing it is sitting under. */
                    waitingOn
                        ? undefined
                        : /* A paused sign-in is answered on another page, so
                             nothing in this pane is going to change until
                             somebody goes there. Saying only "waiting on you"
                             left a blocked run reading as an idle
                             conversation. */
                          signingIn
                          ? "waiting on you to sign in to " + signingIn.host
                          : state === "waiting"
                            ? "waiting on you"
                            : /* Below the run's own notes: a call that did
                                 not start is worth saying, but not in place of
                                 "waiting on you", which is what the reader
                                 has to act on. */
                              shownCallError ?? (pod.waiting || undefined)
                }
                onDismissNote={
                    shownCallError && !waitingOn && !signingIn && state !== "waiting"
                        ? () => setDismissedCallError(shownCallError)
                        : undefined
                }
                /* `sending` lasts as long as the stream this pane opened, which
                   is the whole run -- so it only holds the box before the run
                   is visibly going. After that, sending again is steering. */
                busy={(sending && !running) || historyLoading || Boolean(loadError)}
                canStop={running}
                queued={queued}
                queuedNote={
                    queued.length === 0
                        ? undefined
                        : pod.teammate.name + " hears " + (queued.length === 1 ? "this" : "these") + " as soon as the work in progress allows"
                }
                onWithdraw={id => void withdraw(id)}
                fill={fill}
                onFilled={onFilled}
                attachments={attachments}
                onAttach={attach}
                onRemoveAttachment={unattach}
                onSend={send}
                onStop={() => void session.stop()}
                onVoice={onVoice}
            />
        </>
    );
}
