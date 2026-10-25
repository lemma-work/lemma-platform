import type { SettingsSection } from "@/settings/settings-modal";

/** What any page in the frame can ask of the frame itself: the dialogs the
 *  workspace opens -- settings, connecting a platform, sharing, people --
 *  and putting words in a new chat's box. One place owns them, so a page
 *  deep in a Lemma opens the same settings the sidebar does. */
export interface FrameActions {
    /** The organization's and the reader's settings: account, appearance,
     *  people, connectors, models, usage, billing. */
    openSettings: (section?: SettingsSection) => void;
    /** Where people can reach a Lemma: WhatsApp, Slack, Telegram, email. */
    connect: (podId: string) => void;
    /** Share or export a Lemma. */
    share: (podId: string) => void;
    /** Who has access to a Lemma. */
    addPeople: (podId: string) => void;
    /** Words for a new chat with a Lemma, for the reader to finish and send. */
    prompt: (podId: string, text: string) => void;
    /** Start a chat with a Lemma with these words, sent, and open it --
     *  with one of its agents when `createWith` names one. */
    ask: (podId: string, text: string, createWith?: Record<string, unknown>) => void;
}
