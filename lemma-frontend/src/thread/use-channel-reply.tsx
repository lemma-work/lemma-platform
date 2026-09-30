import { useMemo, useState } from "react";
import { ChannelIcon } from "@/shell/channels";
import { LockIcon } from "@/ui/icons";
import { channelThread, replyChoices, sendMetadata, type ChannelThread, type ReplyMode } from "./channel-reply";
import type { ComposerChoices } from "./composer";

/** The composer's choice in a conversation that also lives on a chat
 *  platform, held by the pane that sends: what it hands the composer to draw,
 *  and what the next message carries. Both panes, live and sample, use this,
 *  so the choice looks and behaves the same in each.
 *
 *  A note by default, and a note again in every other conversation: the
 *  choice is kept against the conversation it was made in, so a pane that is
 *  handed a new one never carries "reply" into a group nobody chose to
 *  answer. */
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
    const [made, setMade] = useState<{ in: string | null; mode: ReplyMode } | null>(null);
    const mode: ReplyMode = made && made.in === conversationId ? made.mode : "note";
    const sendWith = useMemo(() => sendMetadata(thread, mode), [thread, mode]);
    const choices = useMemo<ComposerChoices | undefined>(() => thread ? {
        label: "Where this goes",
        value: mode,
        onChange: (id) => setMade({ in: conversationId, mode: id === "reply" ? "reply" : "note" }),
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
