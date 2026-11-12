import * as FileSystem from "expo-file-system/legacy";
import { freshAccess } from "../../session/auth";
import type { Server } from "../../session/server";

/** Files onto the server from the phone's disk, as the same multipart
 *  requests the SDK makes from a browser (`files.upload`, `files.update`).
 *  The SDK builds them from a Blob, which a phone does not have, so the
 *  platform sends the file from disk.
 *
 *  `data/io.ts` uploads too; this adds what a file area needs beyond it: a
 *  replacement in place (`PATCH …/by-path`), text written from memory, and
 *  failures that keep their HTTP status (`statusCode`), so a 409 can mean
 *  "that name is taken" and a 404 "there is nothing there yet", as on the web. */

export class TransferError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
        super(message);
        this.statusCode = statusCode;
    }
}

function filesUrl(server: Server, podId: string, tail = ""): string {
    return `${server.api}/pods/${encodeURIComponent(podId)}/datastore/files${tail}`;
}

/** A disk file sent as the `data` part, with the other fields beside it. */
async function send(url: string, method: "POST" | "PATCH", uri: string, type: string, fields: Record<string, string>, name: string): Promise<Record<string, unknown>> {
    const access = await freshAccess();
    if (!access) throw new TransferError("Signed out.", 401);
    const result = await FileSystem.uploadAsync(url, uri, {
        httpMethod: method,
        uploadType: FileSystem.FileSystemUploadType.MULTIPART,
        fieldName: "data",
        mimeType: type,
        parameters: fields,
        headers: { Authorization: `Bearer ${access}`, Accept: "application/json" },
    });
    let body: unknown = null;
    try {
        body = JSON.parse(result.body);
    } catch {
        body = result.body;
    }
    if (result.status < 200 || result.status >= 300) {
        const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
        const said = typeof detail === "string" ? detail : "";
        throw new TransferError(said || `${name} could not be saved (${result.status}).`, result.status);
    }
    return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
}

/** A new file in `directory`. A name already taken there is a 409. */
export function createFrom(server: Server, podId: string, file: { uri: string; name: string; type: string }, directory: string, searchEnabled = true) {
    return send(filesUrl(server, podId), "POST", file.uri, file.type, {
        name: file.name,
        directory_path: directory,
        search_enabled: String(searchEnabled),
    }, file.name);
}

/** New bytes for the file at `path`. Nothing there is a 404. */
export function replaceFrom(server: Server, podId: string, path: string, file: { uri: string; type: string }) {
    return send(filesUrl(server, podId, "/by-path"), "PATCH", file.uri, file.type, { path }, path.split("/").pop() ?? path);
}

/** Somewhere in the cache for a file written from memory. */
function scratch(name: string): string {
    const safe = name.replace(/[^\w.\- ]+/g, "_").slice(-80) || "file";
    return `${FileSystem.cacheDirectory}lemma-write-${Date.now()}-${Math.random().toString(36).slice(2, 8)}-${safe}`;
}

/** Hold text or bytes on disk for as long as `use` takes, then let it go. */
export async function onDisk<T>(name: string, contents: string, encoding: "utf8" | "base64", use: (uri: string) => Promise<T>): Promise<T> {
    const uri = scratch(name);
    await FileSystem.writeAsStringAsync(uri, contents, { encoding: encoding === "base64" ? FileSystem.EncodingType.Base64 : FileSystem.EncodingType.UTF8 });
    try {
        return await use(uri);
    } finally {
        void FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
    }
}

/** The type a page or a text file is written back as: from its name, never
 *  from what it was read as, so a file stored as octet-stream is not written
 *  back as one (the web's `writeFile`). */
export function textType(path: string): string {
    return /\.(md|markdown)$/i.test(path) ? "text/markdown" : /\.html?$/i.test(path) ? "text/html" : "text/plain";
}

export async function readBase64(uri: string): Promise<string> {
    return FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 });
}

export function forget(uri: string): void {
    void FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
}
