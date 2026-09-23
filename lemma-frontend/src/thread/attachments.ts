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

export function describeSize(bytes: number): string {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MB";
}
