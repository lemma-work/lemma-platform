import { source, type FileContent } from "@/data";

/** Put a pod file on the reader's disk.
 *
 *  Text this app already read is saved from what it holds; anything else is
 *  fetched with the reader's session. Not `rawUrl`: a signed link opened in a
 *  new tab is the browser's decision to show or save, and for a file this app
 *  could not draw the answer is always save. */
export async function saveFile(podId: string, data: FileContent): Promise<void> {
    const blob = data.text !== undefined
        ? new Blob([data.text], { type: data.mime })
        : await source.downloadFile(podId, data.path);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = data.name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
