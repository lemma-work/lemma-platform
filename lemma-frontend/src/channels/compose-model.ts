/** What the channel composer decides that is not drawing: which key sends,
 *  where a line's files go and what they are called, and what is said about
 *  them on the way. Pure, so the test runner holds it without a page. */

/** Where a line's attachments are uploaded: the writer's own files, private
 *  to them, in a folder of that line's own. Readers reach a file through the
 *  line, which the server serves as the writer can read it -- a pod folder
 *  would let everybody with file access read a private group's files. One
 *  folder per line (its `client_message_id`), so two lines sharing a file
 *  of the same name -- every pasted screenshot is "image.png" -- never meet. */
export function attachmentFolder(channelId: string, clientMessageId: string): string {
    return "/me/channels/" + channelId + "/" + clientMessageId;
}

/** Each file's name within one line, made unique the way a desktop does it:
 *  the second "image.png" is "image (2).png". */
export function uniqueNames(names: readonly string[]): string[] {
    const taken = new Set<string>();
    return names.map((name) => {
        if (!taken.has(name.toLowerCase())) {
            taken.add(name.toLowerCase());
            return name;
        }
        const dot = name.lastIndexOf(".");
        const stem = dot > 0 ? name.slice(0, dot) : name;
        const extension = dot > 0 ? name.slice(dot) : "";
        for (let count = 2; ; count += 1) {
            const candidate = stem + " (" + count + ")" + extension;
            if (!taken.has(candidate.toLowerCase())) {
                taken.add(candidate.toLowerCase());
                return candidate;
            }
        }
    });
}

/** Whether Enter sends the line. Shift+Enter always breaks it, and so does
 *  a composition still being typed. On a touch screen Enter is the
 *  keyboard's return key, a new line, and the send button sends. */
export function enterSends(event: { key: string; shiftKey: boolean; isComposing: boolean }, touch: boolean): boolean {
    return event.key === "Enter" && !event.shiftKey && !event.isComposing && !touch;
}

/** The files carried by a paste or a drop, if any. */
export function carriedFiles(transfer: { files?: ArrayLike<File> | null } | null | undefined): File[] {
    return transfer?.files ? Array.from(transfer.files) : [];
}

/** Whether a drag carries files at all -- so text dragged in is left to the
 *  text box, and only files light the composer up. */
export function dragHasFiles(types: readonly string[] | ArrayLike<string> | null | undefined): boolean {
    return Array.from(types ?? []).includes("Files");
}

export type AttachmentState = "waiting" | "uploading" | "uploaded" | "failed";

/** What a file's chip says after its name. */
export function attachmentSaid(state: AttachmentState): string {
    switch (state) {
        case "waiting": return "";
        case "uploading": return "uploading…";
        case "uploaded": return "uploaded";
        case "failed": return "didn’t upload";
    }
}

/** The line under the chips while a send uploads: "Uploading 2 of 3…". */
export function uploadProgress(states: readonly AttachmentState[]): string | null {
    const at = states.indexOf("uploading");
    if (at === -1) return null;
    return states.length === 1 ? "Uploading the file…" : "Uploading " + (at + 1) + " of " + states.length + "…";
}
