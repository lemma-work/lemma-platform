import { Asset } from "expo-asset";
import { SAMPLE_ORDER, SAMPLE_PHOTOS, SAMPLE_VOICEMAIL } from "./sample-paths";

/** The pictures, the page and the recording the sample chats show: drawn
 *  for the app (`assets/sample/`), shaped like a real Lemma's, never a real
 *  one's. Sample data has no server, so each is a file in the app, read
 *  onto the phone the first time it is shown. */

const MODULES: Record<string, number> = {
    [SAMPLE_PHOTOS[0].path]: require("../../../assets/sample/lamp-cracked.jpg"),
    [SAMPLE_PHOTOS[1].path]: require("../../../assets/sample/lamp-box.jpg"),
    [SAMPLE_PHOTOS[2].path]: require("../../../assets/sample/lamp-shade.jpg"),
    [SAMPLE_PHOTOS[3].path]: require("../../../assets/sample/lamp-label.jpg"),
    [SAMPLE_PHOTOS[4].path]: require("../../../assets/sample/lamp-whole.jpg"),
    "/inbox/northwind/lamp.jpg": require("../../../assets/sample/lamp-cracked.jpg"),
    [SAMPLE_VOICEMAIL]: require("../../../assets/sample/voicemail.m4a"),
};

const FIRST_PAGES: Record<string, number> = {
    [SAMPLE_ORDER]: require("../../../assets/sample/order-page.png"),
};

async function onPhone(module: number | undefined): Promise<string | null> {
    if (module === undefined) return null;
    try {
        const asset = Asset.fromModule(module);
        await asset.downloadAsync();
        return asset.localUri ?? asset.uri;
    } catch {
        return null;
    }
}

export function sampleMediaUrl(path: string): Promise<string | null> {
    return onPhone(MODULES[path]);
}

export function sampleFirstPage(path: string): Promise<string | null> {
    return onPhone(FIRST_PAGES[path]);
}
