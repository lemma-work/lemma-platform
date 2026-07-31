/**
 * Where a share actually goes.
 *
 * Sharing a pod is the growth loop, so the product should hand people a
 * finished post rather than a bare URL and a shrug. Each target here returns a
 * web intent URL that opens the composer pre-filled — no SDKs, no trackers, no
 * network calls from Lemma itself.
 */

export type ShareTargetId =
    | 'x'
    | 'linkedin'
    | 'whatsapp'
    | 'telegram'
    | 'reddit'
    | 'email';

export interface ShareSubject {
    /** What is being shared — a pod, app, agent, workflow or document. */
    name?: string | null;
    /** The canonical link people should open. */
    url: string;
    /** One line about what it does. Falls back to a generic Lemma line. */
    summary?: string | null;
}

const DEFAULT_NAME = 'this pod';

function subjectName(subject: ShareSubject): string {
    return subject.name?.replace(/\s+/g, ' ').trim() || DEFAULT_NAME;
}

/** The headline sentence — used by native share, the clipboard, and every intent. */
export function buildShareTitle(subject: ShareSubject): string {
    return `Run ${subjectName(subject)} on Lemma`;
}

/** The post body. Kept under 200 chars so X never truncates the link. */
export function buildShareText(subject: ShareSubject): string {
    const summary = subject.summary?.replace(/\s+/g, ' ').trim();
    const lead = `${buildShareTitle(subject)}.`;
    if (!summary) return lead;
    const combined = `${lead} ${summary}`;
    return combined.length > 200 ? `${combined.slice(0, 199).trimEnd()}…` : combined;
}

/** The clipboard payload — post text and link on separate lines. */
export function buildShareClipboardText(subject: ShareSubject): string {
    return [buildShareText(subject), subject.url].filter(Boolean).join('\n\n');
}

export interface ShareTarget {
    id: ShareTargetId;
    label: string;
    /** Verb shown in a tooltip, e.g. "Post on X". */
    action: string;
    href: (subject: ShareSubject) => string;
}

export const SHARE_TARGETS: ShareTarget[] = [
    {
        id: 'x',
        label: 'X',
        action: 'Post on X',
        href: (subject) =>
            `https://x.com/intent/tweet?${new URLSearchParams({
                text: buildShareText(subject),
                url: subject.url,
            })}`,
    },
    {
        id: 'linkedin',
        label: 'LinkedIn',
        action: 'Share on LinkedIn',
        // LinkedIn ignores prefilled copy and reads Open Graph tags from the URL,
        // which is exactly what the social-card route exists to serve.
        href: (subject) =>
            `https://www.linkedin.com/sharing/share-offsite/?${new URLSearchParams({
                url: subject.url,
            })}`,
    },
    {
        id: 'whatsapp',
        label: 'WhatsApp',
        action: 'Send on WhatsApp',
        href: (subject) =>
            `https://wa.me/?${new URLSearchParams({
                text: buildShareClipboardText(subject),
            })}`,
    },
    {
        id: 'telegram',
        label: 'Telegram',
        action: 'Send on Telegram',
        href: (subject) =>
            `https://t.me/share/url?${new URLSearchParams({
                url: subject.url,
                text: buildShareText(subject),
            })}`,
    },
    {
        id: 'reddit',
        label: 'Reddit',
        action: 'Post to Reddit',
        href: (subject) =>
            `https://www.reddit.com/submit?${new URLSearchParams({
                title: buildShareTitle(subject),
                url: subject.url,
            })}`,
    },
    {
        id: 'email',
        label: 'Email',
        action: 'Share over email',
        href: (subject) =>
            `mailto:?${new URLSearchParams({
                subject: buildShareTitle(subject),
                body: buildShareClipboardText(subject),
            })}`,
    },
];

export function getShareTarget(id: ShareTargetId): ShareTarget {
    const target = SHARE_TARGETS.find((candidate) => candidate.id === id);
    if (!target) throw new Error(`Unknown share target: ${id}`);
    return target;
}

/**
 * Hands the post to the OS share sheet when there is one, and to the clipboard
 * when there is not. Returns how the share resolved so callers can pick the
 * right confirmation.
 */
export async function shareSubject(
    subject: ShareSubject,
): Promise<'shared' | 'copied' | 'dismissed' | 'failed'> {
    if (typeof navigator !== 'undefined' && typeof navigator.share === 'function') {
        try {
            await navigator.share({
                title: buildShareTitle(subject),
                text: buildShareText(subject),
                url: subject.url,
            });
            return 'shared';
        } catch (error) {
            if (error instanceof DOMException && error.name === 'AbortError') return 'dismissed';
            // Fall through to the clipboard — some browsers advertise share and refuse it.
        }
    }

    try {
        await navigator.clipboard.writeText(buildShareClipboardText(subject));
        return 'copied';
    } catch {
        return 'failed';
    }
}
