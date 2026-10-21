/** The reader's own settings in a channel, said: where they hear of it
 *  outside the app, and how long it is muted for. Pure, for the tests. */

/** The platforms a member can ask to hear of a channel on. Whether one
 *  reaches them is the pod's: one they have never spoken to the pod on is
 *  passed over for the next way it can reach them. */
export const NOTIFY_VIA: readonly { value: string; label: string }[] = [
    { value: "EMAIL", label: "Email" },
    { value: "SLACK", label: "Slack" },
    { value: "TEAMS", label: "Teams" },
    { value: "WHATSAPP", label: "WhatsApp" },
    { value: "TELEGRAM", label: "Telegram" },
];

const HOUR = 3_600_000;

/** The mutes offered, each as the moment it ends. */
export function muteChoices(now = new Date()): { label: string; until: string }[] {
    const at = (ms: number) => new Date(now.getTime() + ms).toISOString();
    return [
        { label: "For an hour", until: at(HOUR) },
        { label: "For 8 hours", until: at(8 * HOUR) },
        { label: "For a day", until: at(24 * HOUR) },
        { label: "For a week", until: at(7 * 24 * HOUR) },
    ];
}

/** "Muted until 14:30", "Muted until Fri 9 Oct" -- or null when it is not
 *  muted, or the mute has run out. */
export function mutedSaid(until: string | null, now = new Date()): string | null {
    if (!until) return null;
    const at = new Date(until);
    if (Number.isNaN(at.getTime()) || at.getTime() <= now.getTime()) return null;
    const sameDay = at.toDateString() === now.toDateString();
    return "Muted until " + (sameDay
        ? at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
        : at.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }));
}

/** Whether a group shows its agents' answers to everybody in it: the
 *  switch's label, and what each way means. Asked in front of the group, an
 *  answer is the group's to read; a group can keep one to whoever asked
 *  instead, until they share it (the "Share with channel" on the line). */
export const ANSWERS_SHARED_LABEL = "Show agents’ answers to everyone here";

export function answersSharedNote(shared: boolean): string {
    return shared
        ? "Everyone here reads what an agent answers, even when it used the asker’s own access."
        : "Keep an answer to whoever asked until they share it. Everyone else sees that it was answered, not what it said.";
}

/** What making a private group public means, asked before it is done: the
 *  whole pod can read it from then on, what was said before included. */
export function publicWarning(title: string | null, space: string): string {
    return "Everyone in " + space + " will be able to find " + (title ?? "this chat")
        + " and read everything said in it, including what was said before it was public. Make it public?";
}
