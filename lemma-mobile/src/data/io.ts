import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system/legacy";
import * as ImagePicker from "expo-image-picker";
import * as Sharing from "expo-sharing";
import { authedFetch, freshAccess } from "../session/auth";
import type { Server } from "../session/server";

/** Files in and out on a phone, where the SDK's own calls do not work:
 *
 *  - Reading: `files.download` answers a Blob, which React Native cannot
 *    always read, so text is read from the response directly.
 *  - Saving: a file is downloaded into the app's cache, where the share sheet
 *    can hand it to another app or to Files.
 *  - Uploading: the SDK builds a browser `FormData` from a Blob; a phone has
 *    a file on disk instead, so the platform uploads it, as the same
 *    multipart request (`POST /pods/{pod}/datastore/files`).
 *
 *  Images, video and PDFs are shown from a signed URL (`files.getUrl`),
 *  which needs no headers, rather than downloaded here. */

/** A file the person chose, on this phone's disk. */
export interface PickedFile {
    uri: string;
    name: string;
    /** Its MIME type, `application/octet-stream` when the picker did not say. */
    type: string;
    size: number | null;
}

function fileUrl(server: Server, podId: string, path: string): string {
    return `${server.api}/pods/${encodeURIComponent(podId)}/datastore/files/download?path=${encodeURIComponent(path)}`;
}

/** The text of a pod file, or null when there is no such file. Any other
 *  failure throws, so a file that could not be read is never mistaken for
 *  one that is not there. */
export async function readText(server: Server, podId: string, path: string): Promise<string | null> {
    const response = await authedFetch(fileUrl(server, podId, path), { headers: { Accept: "*/*" } });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${path} could not be read (${response.status}).`);
    return await response.text();
}

/** Somewhere in the cache for a file of this name, safe on every platform. */
function cachePath(name: string): string {
    const safe = name.replace(/[^\w.\- ]+/g, "_").slice(-120) || "file";
    return `${FileSystem.cacheDirectory}lemma-${Date.now()}-${safe}`;
}

/** Download a pod file onto the phone; answers its local URI. */
export async function saveToPhone(server: Server, podId: string, path: string): Promise<string> {
    const access = await freshAccess();
    if (!access) throw new Error("Signed out.");
    const name = path.split("/").pop() ?? "file";
    const result = await FileSystem.downloadAsync(fileUrl(server, podId, path), cachePath(name), {
        headers: { Authorization: `Bearer ${access}`, Accept: "*/*" },
    });
    if (result.status === 404) throw new Error(`${name} is not there any more.`);
    if (result.status < 200 || result.status >= 300) throw new Error(`${name} could not be downloaded (${result.status}).`);
    return result.uri;
}

/** The share sheet for a file on the phone: open it in another app, save it
 *  to Files, send it. */
export async function shareFile(uri: string, type?: string): Promise<void> {
    if (!(await Sharing.isAvailableAsync())) throw new Error("This phone cannot share files from here.");
    await Sharing.shareAsync(uri, type ? { mimeType: type } : undefined);
}

/** Upload a file from the phone into a pod folder. Answers the server's file
 *  record, as `files.upload` would. */
export async function uploadFile(
    server: Server,
    podId: string,
    file: PickedFile,
    options: { directoryPath: string; searchEnabled?: boolean; description?: string },
): Promise<unknown> {
    const access = await freshAccess();
    if (!access) throw new Error("Signed out.");
    const parameters: Record<string, string> = {
        name: file.name,
        directory_path: options.directoryPath,
        search_enabled: String(options.searchEnabled ?? true),
    };
    if (options.description) parameters.description = options.description;
    const result = await FileSystem.uploadAsync(`${server.api}/pods/${encodeURIComponent(podId)}/datastore/files`, file.uri, {
        httpMethod: "POST",
        uploadType: FileSystem.FileSystemUploadType.MULTIPART,
        fieldName: "data",
        mimeType: file.type,
        parameters,
        headers: { Authorization: `Bearer ${access}`, Accept: "application/json" },
    });
    let body: unknown = null;
    try {
        body = JSON.parse(result.body);
    } catch {
        body = result.body;
    }
    if (result.status < 200 || result.status >= 300) {
        const detail = body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : "";
        throw new Error(`${file.name} could not be uploaded (${result.status})${detail ? `: ${detail}` : "."}`);
    }
    return body;
}

const UNKNOWN_TYPE = "application/octet-stream";

/** Files from the phone's files app, any kind, several at once. */
export async function pickFiles(): Promise<PickedFile[]> {
    const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (result.canceled) return [];
    return result.assets.map((asset) => ({ uri: asset.uri, name: asset.name, type: asset.mimeType ?? UNKNOWN_TYPE, size: asset.size ?? null }));
}

function fromImage(asset: ImagePicker.ImagePickerAsset, index: number): PickedFile {
    const type = asset.mimeType ?? (asset.type === "video" ? "video/mp4" : "image/jpeg");
    const extension = type.split("/")[1]?.replace("jpeg", "jpg") ?? "jpg";
    return { uri: asset.uri, name: asset.fileName ?? `photo-${Date.now()}-${index + 1}.${extension}`, type, size: asset.fileSize ?? null };
}

/** Photos and videos from the library, several at once. */
export async function pickPhotos(): Promise<PickedFile[]> {
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images", "videos"], allowsMultipleSelection: true, quality: 0.9 });
    if (result.canceled) return [];
    return result.assets.map(fromImage);
}

/** A photo taken now. Null when the person cancels or refuses the camera. */
export async function takePhoto(): Promise<PickedFile | null> {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) throw new Error("The camera is off for Lemma. Turn it on in the phone's settings.");
    const result = await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], quality: 0.9 });
    if (result.canceled) return null;
    return result.assets[0] ? fromImage(result.assets[0], 0) : null;
}

/** One picture from the library, cropped square, for a face. Null when the
 *  person cancels. JPEG at a size the server's icon limit (5 MB, 4096 px)
 *  never refuses. */
export async function pickSquarePhoto(): Promise<PickedFile | null> {
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], allowsEditing: true, aspect: [1, 1], quality: 0.8 });
    if (result.canceled || !result.assets[0]) return null;
    return fromImage(result.assets[0], 0);
}

/** Upload a picture as an icon (`POST /icons/upload`, the SDK's
 *  `icons.upload`, which builds a browser Blob a phone does not have).
 *  Answers the public URL to store as an `icon_url`. */
export async function uploadIcon(server: Server, file: PickedFile): Promise<string> {
    const access = await freshAccess();
    if (!access) throw new Error("Signed out.");
    const result = await FileSystem.uploadAsync(`${server.api}/icons/upload`, file.uri, {
        httpMethod: "POST",
        uploadType: FileSystem.FileSystemUploadType.MULTIPART,
        fieldName: "file",
        mimeType: file.type,
        headers: { Authorization: `Bearer ${access}`, Accept: "application/json" },
    });
    let body: { icon_url?: string; detail?: unknown } = {};
    try {
        body = JSON.parse(result.body) as typeof body;
    } catch {
        /* Not JSON: said by status below. */
    }
    if (result.status < 200 || result.status >= 300) {
        const detail = typeof body.detail === "string" ? body.detail : "";
        throw new Error(detail || `The picture could not be uploaded (${result.status}).`);
    }
    if (!body.icon_url) throw new Error("The upload came back without an address.");
    return body.icon_url;
}
