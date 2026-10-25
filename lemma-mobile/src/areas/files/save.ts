import { Platform } from "react-native";
import * as FileSystem from "expo-file-system/legacy";
import * as IntentLauncher from "expo-intent-launcher";
import { shareFile } from "../../data/io";

/** A file that is already on the phone (`FilesSource.toPhone`), handed on:
 *  kept somewhere the person chooses, or opened in another app. The web's
 *  `save-file.ts` downloads; a phone has two different things to offer. */

/** Keep a copy. Android asks for a folder (Downloads, Drive, anywhere the
 *  system offers) and writes the file there; iOS has no folder to ask for,
 *  and its share sheet's "Save to Files" is the same act. Answers what was
 *  done, to say it, or null when the person backed out. */
export async function keepCopy(uri: string, name: string, mime: string): Promise<string | null> {
    if (Platform.OS !== "android") {
        await shareFile(uri, mime);
        return null;
    }
    const SAF = FileSystem.StorageAccessFramework;
    const granted = await SAF.requestDirectoryPermissionsAsync();
    if (!granted.granted) return null;
    const dot = name.lastIndexOf(".");
    const made = await SAF.createFileAsync(granted.directoryUri, dot > 0 ? name.slice(0, dot) : name, mime || "application/octet-stream");
    const base64 = await FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 });
    await SAF.writeAsStringAsync(made, base64, { encoding: FileSystem.EncodingType.Base64 });
    return `${name} is saved.`;
}

/** Open it in whichever app on the phone reads this kind of file. Android
 *  asks the system to view it; iOS offers "Open in…" from its share sheet. */
export async function openElsewhere(uri: string, mime: string): Promise<void> {
    if (Platform.OS !== "android") {
        await shareFile(uri, mime);
        return;
    }
    const content = await FileSystem.getContentUriAsync(uri);
    try {
        await IntentLauncher.startActivityAsync("android.intent.action.VIEW", {
            data: content,
            type: mime || "*/*",
            /* FLAG_GRANT_READ_URI_PERMISSION: the other app may read it. */
            flags: 1,
        });
    } catch {
        throw new Error("No app on this phone opens this kind of file.");
    }
}
