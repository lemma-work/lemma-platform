import { useEffect, useState } from "react";
import { Asset } from "expo-asset";
import * as FileSystem from "expo-file-system/legacy";

/** The app's own face, for a page drawn inside it: a widget is told to set
 *  its words in "Albert Sans" (the theme's `font` token), and a WebView has
 *  no such font, so it fell back to the system's. The theme message cannot
 *  carry a font -- the widget refuses any `url(` in a token -- so the frame
 *  puts this `@font-face` into the widget's own page instead, from the same
 *  two files the app is built with (`expo-font` in app.json), as data.
 *
 *  Read once per launch. Empty when it cannot be read, and the widget then
 *  draws as it did before. */

const FACES: { weight: number; module: number }[] = [
    { weight: 400, module: require("@expo-google-fonts/albert-sans/400Regular/AlbertSans_400Regular.ttf") },
    { weight: 500, module: require("@expo-google-fonts/albert-sans/500Medium/AlbertSans_500Medium.ttf") },
];

let reading: Promise<string> | null = null;
let read: string | null = null;

export function widgetFontCss(): Promise<string> {
    reading ??= (async () => {
        const faces = await Promise.all(FACES.map(async (face) => {
            const asset = Asset.fromModule(face.module);
            await asset.downloadAsync();
            if (!asset.localUri) throw new Error("No font file.");
            const data = await FileSystem.readAsStringAsync(asset.localUri, { encoding: FileSystem.EncodingType.Base64 });
            return `@font-face{font-family:"Albert Sans";font-style:normal;font-weight:${face.weight};font-display:block;src:url(data:font/ttf;base64,${data}) format("truetype")}`;
        }));
        return faces.join("\n");
    })().catch(() => "").then((css) => (read = css));
    return reading;
}

/** The CSS, once it has been read: null until then, "" when it could not be. */
export function useWidgetFont(): string | null {
    const [css, setCss] = useState<string | null>(read);
    useEffect(() => {
        if (css !== null) return;
        let gone = false;
        void widgetFontCss().then((value) => { if (!gone) setCss(value); });
        return () => { gone = true; };
    }, [css]);
    return css;
}
