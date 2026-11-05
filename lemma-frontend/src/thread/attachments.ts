/** Files a person attached to a message, on their way to the teammate.
 *
 *  Where they land is the whole design, and it is the server's decision rather
 *  than this app's. A conversation carries a `pod_cwd` — its own working
 *  directory in pod files, the one the agent's tools resolve a relative path
 *  against. Upload there and somebody attaching `report.pdf` gets an agent that
 *  finds it by that name, with no path to be told.
 *
 *  The SDK's own controller records what happened when a client computed that
 *  directory instead of reading it: uploads landed under
 *  `/me/conversations/{uuid}`, somewhere the agent's cwd never pointed, and were
 *  findable only because the client pasted the absolute path into the message
 *  text. So: read `pod_cwd`, never rebuild it.
 */

export type AttachmentStatus = "queued" | "uploading" | "uploaded" | "failed";

export interface Attachment {
    /** Stable across renders and across two files with the same name. */
    key: string;
    file: File;
    status: AttachmentStatus;
    /** Where it landed. Set once uploaded. */
    path?: string;
    error?: string;
}

/** A file that has been written to the pod, as the message needs to name it. */
export interface UploadedFile {
    name?: string | null;
    path: string;
}

let counter = 0;

/** What a clipboard image is called, and why it needs another name.
 *
 *  A pasted screenshot arrives with no name of its own, so every browser calls
 *  it `image.png` — or `image (1).png` if the desktop already had one. Two
 *  pastes in one message are therefore two files called the same thing, and a
 *  folder that already holds an `image.png` has nowhere to put either. Naming
 *  them here, before they are offered, is what keeps the upload from being the
 *  place this is discovered.
 */
const PASTED_STEM = "pasted";

/** The extension a pasted file should keep, from its own name if it has one
 *  and from its type if it does not: a clipboard image is often called
 *  `image` with no extension at all, and a file stored without one cannot be
 *  read back as an image by anything. */
function pastedExtension(file: File): string {
    const named = /\.[A-Za-z0-9]{1,8}$/.exec(file.name ?? "");
    if (named) return named[0].toLowerCase();
    const byType: Record<string, string> = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "application/pdf": ".pdf",
    };
    return byType[file.type] ?? "";
}

/** The stamp a pasted file is named with: local time, because it is a name a
 *  person reads and compares with when they took the screenshot. */
function stampOf(at: Date): string {
    const pad = (value: number) => String(value).padStart(2, "0");
    return (
        String(at.getFullYear()) + pad(at.getMonth() + 1) + pad(at.getDate()) +
        "-" + pad(at.getHours()) + pad(at.getMinutes()) + pad(at.getSeconds())
    );
}

/* Two pastes inside one second are two pastes, and the second one has to be
   named as well as the first. The counter is kept across calls rather than
   per call, because the two arrive as two paste events, and it counts the
   files named rather than the pastes: a paste of two then a paste of one in
   the same second would otherwise give the third the second's name. */
let lastStamp = "";
let namedThisSecond = 0;

/** Name pasted files so no two of them collide: `pasted-20261017-064158.png`,
 *  and `…-2.png` for a second one in the same second. */
export function namePasted(files: readonly File[], at: Date = new Date()): File[] {
    const stamp = stampOf(at);
    if (stamp !== lastStamp) {
        lastStamp = stamp;
        namedThisSecond = 0;
    }
    const first = namedThisSecond;
    namedThisSecond += files.length;
    return files.map((file, index) => {
        const count = first + index + 1;
        const suffix = count === 1 ? "" : "-" + count;
        const name = PASTED_STEM + "-" + stamp + suffix + pastedExtension(file);
        /* A new File rather than a renamed one: the name is the whole of what
           the upload and the chip read, and a File is immutable. */
        return new File([file], name, { type: file.type, lastModified: file.lastModified });
    });
}

/** A key that survives two files called the same thing.
 *
 *  Name plus size plus modified-time collides for a duplicate of the same file,
 *  which people do attach — twice by accident, and then remove one. A counter
 *  is the only thing that cannot.
 */
export function attachmentKey(file: File): string {
    counter += 1;
    return counter + ":" + file.name;
}

export function toAttachments(files: readonly File[]): Attachment[] {
    return files.map((file) => ({ key: attachmentKey(file), file, status: "queued" as const }));
}

export function withReferences(content: string, files: readonly UploadedFile[]): string {
    if (files.length === 0) return content;
    const references = files
        .map((file) => {
            const parts = file.path.split("/").filter(Boolean);
            const name = file.name || parts[parts.length - 1] || file.path;
            return "- " + name + ": " + file.path;
        })
        .join("\n");
    return content + "\n\nPersonal files available to this run:\n" + references;
}

/** What to say when somebody attached a file and typed nothing.
 *
 *  An empty message with an attachment is a real thing to send — "here, look at
 *  this" — and an agent handed an empty string has been told nothing at all.
 */
export const NOTHING_TYPED = "Please use the attached files.";

export function contentFor(draft: string, attachments: readonly Attachment[]): string {
    const trimmed = draft.trim();
    if (trimmed) return trimmed;
    return attachments.length > 0 ? NOTHING_TYPED : "";
}

/** Is there anything here to send? */
export function canSend(draft: string, attachments: readonly Attachment[]): boolean {
    return draft.trim().length > 0 || attachments.length > 0;
}

/** Is this one already in the pod?
 *
 *  It can be, without the message having gone: the upload succeeds, the send
 *  that follows it fails, and the file is sitting in `pod_cwd` while the chip
 *  comes back to the composer. Uploading it again on the retry would leave two
 *  copies of one file, and the second would take the name.
 */
export function isAlreadyUploaded(one: Attachment): one is Attachment & { path: string } {
    return one.status === "uploaded" && typeof one.path === "string" && one.path.length > 0;
}

export function markAttachment(
    attachments: readonly Attachment[],
    key: string,
    change: Partial<Omit<Attachment, "key" | "file">>,
): Attachment[] {
    return attachments.map((one) => (one.key === key ? { ...one, ...change } : one));
}

/** How big a single attachment may be.
 *
 *  A guess at the server's ceiling rather than knowledge of it — the limit is
 *  enforced there and this only decides whether to spend somebody's upload on
 *  finding out. Refusing early is kinder than a minute of progress bar ending
 *  in a 413, and refusing at the wrong number is only ever a false negative
 *  that says exactly what it did.
 */
export const MAX_BYTES = 100 * 1024 * 1024;

export function tooLarge(file: File): boolean {
    return file.size > MAX_BYTES;
}

/** What a drop, a paste or the file picker handed over, split into what can go
 *  up and a sentence about what cannot.
 *
 *  Shared by the composer and the ask box, because the two are the same act on
 *  two surfaces: a second copy of this rule is a second ceiling, and the one
 *  that drifts is the one somebody hits.
 */
export function offered(files: readonly File[]): { take: File[]; refused: string | null } {
    const big = files.filter(tooLarge);
    const take = files.filter((file) => !tooLarge(file));
    return {
        take,
        refused:
            big.length === 0
                ? null
                : big.length === 1
                  ? big[0].name + " is too large to attach (" + describeSize(big[0].size) + ")."
                  : big.length + " files are too large to attach.",
    };
}

export function describeSize(bytes: number): string {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MB";
}
