import { useMemo, useState } from "react";
import { ChannelIcon } from "@/shell/channels";
import { LockIcon } from "@/ui/icons";
import {
    browserStore,
    channelThread,
    rememberReplyMode,
    rememberedReplyMode,
    replyChoices,
    replyModeFor,
    sendMetadata,
    type ChannelThread,
    type ReplyMode,
} from "./channel-reply";
import type { ComposerChoices } from "./composer";

/** The composer's choice in a conversation that also lives on a chat
 *  platform, held by the pane that sends: what it hands the composer to draw,
 *  and what the next message carries. Both panes, live and sample, use this,
 *  so the choice looks and behaves the same in each.
 *
 *  Each conversation starts on its own default — a reply in a person's own
 *  DM or email thread, a note in a group or a channel — and then on whatever
 *  was last chosen in it, kept in this browser. A choice is never carried
 *  from one conversation into another, so a pane that is handed a new one
 *  never brings "reply" into a group nobody chose to answer. */
export function useChannelReply(
    metadata: Record<string, unknown> | null | undefined,
    bot: string,
    conversationId: string | null,
): {
    thread: ChannelThread | null;
    /** The next message's metadata: `{ private_note: true }`, or nothing. */
    sendWith: Record<string, unknown> | undefined;
    choices: ComposerChoices | undefined;
    /** What the box says while a choice is on screen. */
    placeholder: string | undefined;
} {
    const thread = useMemo(() => channelThread(metadata), [metadata]);
    /* Chosen since this page opened, against the conversation it was chosen
       in; and before that, whatever this browser kept for the conversation. */
    const [made, setMade] = useState<{ in: string | null; mode: ReplyMode } | null>(null);
    const remembered = useMemo(
        () => (thread ? rememberedReplyMode(browserStore(), conversationId) : null),
        [thread, conversationId],
    );
    const mode: ReplyMode = made && made.in === conversationId ? made.mode
        : thread ? replyModeFor(thread, remembered)
        : "note";
    const sendWith = useMemo(() => sendMetadata(thread, mode), [thread, mode]);
    const choices = useMemo<ComposerChoices | undefined>(() => thread ? {
        label: "Where this goes",
        value: mode,
        onChange: (id) => {
            const next: ReplyMode = id === "reply" ? "reply" : "note";
            setMade({ in: conversationId, mode: next });
            rememberReplyMode(browserStore(), conversationId, next);
        },
        options: replyChoices(thread, bot).map((choice) => ({
            id: choice.mode,
            label: choice.label,
            hint: choice.hint,
            icon: choice.mode === "note"
                ? <LockIcon size={14} aria-hidden="true" />
                : <ChannelIcon platform={thread.platform} size={14} />,
        })),
    } : undefined, [thread, bot, mode, conversationId]);
    const placeholder = choices?.options.find((option) => option.id === mode)?.label;
    return { thread, sendWith, choices, placeholder: placeholder && placeholder + "…" };
}
