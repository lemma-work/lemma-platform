import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { source } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import type { Message, Pod } from "@/data";
import type { ApprovalDecision } from "./approval";
import { NEW_CONVERSATION } from "@/data";
import { buildTurns, openInteraction } from "./turns";
import { Transcript } from "./transcript";
import { Composer } from "./composer";
import { InteractionDock } from "./interaction-dock";
import { toAttachments, type Attachment } from "./attachments";

/** How much of the sample history one "Earlier" hands back. Small, because the
 *  point is to reach the top in a few presses rather than to be realistic. */
const PAGE = 8;

/** The sample pane. It reads a plain conversation instead of holding a live
 *  session, but goes through the same turn builder and the same transcript —
 *  a stand-in that renders differently from the real thing is worth nothing. */
export function ConversationPane({
    pod,
    conversationId,
    fill,
    onFilled,
    onOpenApp,
    onOpenFile,
    onOpenTable,
}: {
    pod: Pod;
    conversationId?: string | null;
    /** The ways onto the stage. The live pane has always had them and this one
     *  had none, so every card in the only mode that opens without a session
     *  drew its "there is nowhere to open this" variant — a file card with no
     *  way to a tab, and a framed page whose one control was "make it taller
     *  here". Neither was a decision; they were three props that stopped at
     *  the shell. */
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    /** The same composer fill the live pane takes. Wired here too so the
     *  sample source demonstrates the whole path with no backend behind it —
     *  a widget asks, the box fills, and only the send is pretend. */
    fill?: { text: string; id: number } | null;
    onFilled?: () => void;
    onCreated?: (id: string) => void;
}) {
    const [error, setError] = useState<string | null>(null);

    /* Attaching works here as far as it can go: files are held, listed and
       removable, and only the send is pretend — which is exactly what the rest
       of this pane already does. Withholding the control instead would leave
       the chips, the drop target and the size refusal unlooked-at, since this
       is the only mode that opens without a session. */
    const [attachments, setAttachments] = useState<Attachment[]>([]);

    /* The sample has no cursor to walk, so it walks its own history: the pane
       holds how far back it has been asked to go and hands the transcript the
       tail. The live pane pages a real token, but what the transcript is given
       — some messages, a flag saying there are older ones, and a way to ask —
       is the same, which is the only reason checking it here means anything. */
    const [reach, setReach] = useState(PAGE);

    /* Decisions taken here, kept in memory. A sample that draws the card but
       cannot answer it is a screenshot, and the states worth judging — the
       wait between clicking and the tool actually returning, and what the
       card settles into afterwards — are exactly the ones a screenshot
       cannot show. */
    const [answered, setAnswered] = useState<Message[]>([]);
    const resolve = useCallback<
        (id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => Promise<void>
    >(async (id, decision, response) => {
        await new Promise((done) => setTimeout(done, 700));
        setAnswered((was) => [
            ...was,
            {
                id: "sample-return-" + id,
                role: "assistant",
                kind: "TOOL_RETURN",
                tool_call_id: id,
                tool_result: { decision, ...(response ?? {}) },
                sequence: 10_000 + was.length,
                created_at: new Date().toISOString(),
            },
        ]);
    }, []);
    const conversation = useQuery({
        queryKey: ["sample-conversation", pod.id, conversationId ?? "latest"],
        queryFn: () => source.getConversation(pod.id, pod.teammate, conversationId),
        staleTime: 5 * 60_000,
    });
    const all = conversation.data?.messages ?? [];
    const shown = useMemo(() => all.slice(Math.max(0, all.length - reach)), [all, reach]);
    const turns = useMemo(() => buildTurns([...shown, ...answered]), [shown, answered]);

    /* The same read the live pane makes, for the same shelf. Wiring it here is
       not a courtesy to the sample: this is the only pane that opens without a
       session, so a docked card the sample cannot draw is a docked card nobody
       looks at until an agent happens to ask for something. */
    const waitingOn = useMemo(() => openInteraction(turns), [turns]);

    const open = conversationId && conversationId !== NEW_CONVERSATION ? conversationId : conversation.data?.id ?? null;

    return (
        <>
            <Transcript
                turns={turns}
                teammate={pod.teammate}
                streaming={null}
                state="idle"
                error={error ?? (conversation.isError ? "Could not read this conversation." : null)}
                emptyTitle={conversation.isPending ? "Opening conversation…" : conversationId === NEW_CONVERSATION ? "New conversation" : "Nothing said in here yet"}
                podId={pod.id}
                /* The live pane hands this down and this one did not, which
                   meant every card keyed to a conversation — a paused sign-in
                   most of all, whose only control is a link built from it —
                   rendered its "I do not know which conversation this is"
                   state in the one mode anybody can open. */
                conversationId={open}
                hasMore={all.length > reach}
                onEarlier={() => {
                    if (all.length <= reach) return false;
                    setReach((was) => was + PAGE);
                    return true;
                }}
                onResolve={resolve}
                dockedId={waitingOn?.id}
                onOpenApp={onOpenApp}
                onOpenFile={onOpenFile}
                onOpenTable={onOpenTable}
                emptyBody={conversation.isPending ? "" : pod.teammate.name + " is ready. Send a message to start."}
            />
            <InteractionDock interaction={waitingOn} teammate={pod.teammate.name} onResolve={resolve} />
            <Composer
                placeholder={"Talk to " + pod.name + "…"}
                note={waitingOn || isLandingPreview() ? undefined : pod.waiting || undefined}
                busy={false}
                canStop={false}
                fill={fill}
                onFilled={onFilled}
                attachments={attachments}
                onAttach={(files) => setAttachments(was => [...was, ...toAttachments(files)])}
                onRemoveAttachment={(key) => setAttachments(was => was.filter(one => one.key !== key))}
                onSend={() => setError("This is the sample source — connect a session to send anything.")}
            />
        </>
    );
}
