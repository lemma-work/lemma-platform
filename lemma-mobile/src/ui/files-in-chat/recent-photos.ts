import * as FileSystem from "expo-file-system/legacy";
import type { PickedFile } from "../../data/io";

/** The phone's latest photos, for the attach tray: read from its library
 *  with the person's leave, newest first.
 *
 *  The library is a native module, so it is loaded only when asked for: a
 *  build of the app without it still attaches from the camera, the system's
 *  photo picker and files, and says nothing about what it lacks. */

type Library = typeof import("expo-media-library/legacy");

let loaded: Library | null | undefined;

function library(): Library | null {
    if (loaded !== undefined) return loaded;
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        loaded = require("expo-media-library/legacy") as Library;
    } catch {
        loaded = null;
    }
    return loaded;
}

export interface RecentPhoto {
    id: string;
    /** What a thumbnail draws: a file, or the library's own address. */
    uri: string;
    name: string;
}

export type Recent =
    | { kind: "none" }
    | { kind: "ask" }
    | { kind: "refused" }
    | { kind: "ready"; photos: RecentPhoto[] };

/** The latest `count` photos, asking for leave first when `ask` and it was
 *  never asked. */
export async function recentPhotos(ask: boolean, count = 24): Promise<Recent> {
    const media = library();
    if (!media) return { kind: "none" };
    try {
        let leave = await media.getPermissionsAsync(false, ["photo"]);
        if (!leave.granted && leave.canAskAgain && ask) leave = await media.requestPermissionsAsync(false, ["photo"]);
        if (!leave.granted) return leave.canAskAgain ? { kind: "ask" } : { kind: "refused" };
        const page = await media.getAssetsAsync({ first: count, mediaType: media.MediaType.photo, sortBy: [[media.SortBy.creationTime, false]] });
        return { kind: "ready", photos: page.assets.map((asset) => ({ id: asset.id, uri: asset.uri, name: asset.filename })) };
    } catch {
        return { kind: "none" };
    }
}

const TYPES: Record<string, string> = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", heic: "image/heic", heif: "image/heif", webp: "image/webp", gif: "image/gif" };

/** A photo from the library, as a file this phone can upload. */
export async function pickedFrom(photo: RecentPhoto): Promise<PickedFile> {
    let uri = photo.uri;
    const media = library();
    /* iOS hands out the library's own address; the file is behind it. */
    if (!uri.startsWith("file:") && media) {
        const info = await media.getAssetInfoAsync(photo.id);
        if (info.localUri) uri = info.localUri;
    }
    const extension = (photo.name.split(".").pop() ?? "").toLowerCase();
    let size: number | null = null;
    try {
        const info = await FileSystem.getInfoAsync(uri);
        size = info.exists && typeof info.size === "number" ? info.size : null;
    } catch {
        size = null;
    }
    return { uri, name: photo.name, type: TYPES[extension] ?? "image/jpeg", size };
}
