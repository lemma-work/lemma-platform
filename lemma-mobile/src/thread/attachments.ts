/** Files a person attached to a message, on their way to the Lemma.
 *
 *  The web's `attachments.ts`, for a phone: a file is one the person picked
 *  on this phone (`PickedFile`, a path on its disk) rather than a browser
 *  `File`, and nothing is pasted. What stays exactly the same is where a file
 *  lands and what the message says about it, since that is what the agent
 *  reads:
 *
 *  - Into the conversation's own working directory, `pod_cwd`, read off the
 *    conversation and never rebuilt. The agent's tools resolve a relative
 *    path against it, so `report.pdf` is found by that name.
 *  - Named at the end of the message, "Personal files available to this
 *    run:", one line per file (`withReferences`).
 *
 *  Pure, so the tests read it directly. */

export interface PickedFileLike {
    uri: string;
    name: string;
    type: string;
    size: number | null;
}

export type AttachmentStatus = "queued" | "uploading" | "uploaded" | "failed";

export interface Attachment {
    /** Stable across renders and across two files with the same name. */
    key: string;
    file: PickedFileLike;
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

/** A key that survives two files called the same thing: a counter is the
 *  only thing two copies of one photo cannot share. */
export function attachmentKey(file: PickedFileLike): string {
    counter += 1;
    return counter + ":" + file.name;
}

export function toAttachments(files: readonly PickedFileLike[]): Attachment[] {
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

/** What to say when somebody attached a file and typed nothing: "here, look
 *  at this" is a real message, and an empty string tells the agent nothing. */
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

/** Already in the pod: the upload went, the send after it did not. A retry
 *  names it rather than uploading a second copy that would take the name. */
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

/** The web's guess at the server's ceiling: refusing early is kinder than a
 *  minute of upload ending in a 413. */
export const MAX_BYTES = 100 * 1024 * 1024;

export function tooLarge(file: PickedFileLike): boolean {
    return (file.size ?? 0) > MAX_BYTES;
}

/** What the picker handed over, split into what can go up and a sentence
 *  about what cannot. */
export function offered<T extends PickedFileLike>(files: readonly T[]): { take: T[]; refused: string | null } {
    const big = files.filter(tooLarge);
    const take = files.filter((file) => !tooLarge(file));
    return {
        take,
        refused:
            big.length === 0
                ? null
                : big.length === 1
                  ? big[0].name + " is too large to attach (" + describeSize(big[0].size ?? 0) + ")."
                  : big.length + " files are too large to attach.",
    };
}

export function describeSize(bytes: number): string {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MB";
}

/** The sentence for an upload that failed, the file named first: with two
 *  attached, "upload failed" leaves the reader to guess which. */
export function uploadFailure(name: string, reason: string): string {
    return "Couldn’t upload " + name + "." + (reason ? " " + reason : "");
}
