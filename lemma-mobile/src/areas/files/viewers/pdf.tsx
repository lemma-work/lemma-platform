import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { useQuery } from "@tanstack/react-query";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useFilesKey, useFilesSource } from "../hooks";
import { HTML as PDF_PAGE } from "../web/pdf-page.generated";

/** A PDF, drawn by pdf.js in a WebView (`webview/src/pdf`): Android's
 *  WebView shows no PDF on its own. The app reads the bytes with its own
 *  session and hands them over in pieces; the page never fetches anything. */

/** Base64 characters per message: large enough to be quick, small enough
 *  for the bridge. */
const PIECE = 512 * 1024;

/** The page's origin: its own, never the API's or the web app's. */
const ORIGIN = "https://pdf.lemma.invalid/";

export function PdfView({ podId, path, name }: { podId: string; path: string; name: string }) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const view = useRef<WebView>(null);
    const [ready, setReady] = useState(false);
    const [pages, setPages] = useState<number | null>(null);
    const [at, setAt] = useState(1);
    const [problem, setProblem] = useState<string | null>(null);
    const bytes = useQuery({ queryKey: keyOf("bytes", path), queryFn: () => source.bytes(podId, path), staleTime: 5 * 60_000, gcTime: 60_000, retry: 1 });

    const say = (message: unknown) => view.current?.injectJavaScript(`window.__fromApp && window.__fromApp(${JSON.stringify(message)});true;`);

    useEffect(() => {
        if (!ready || !bytes.data) return;
        say({ type: "theme", paper: color.paper, ink3: color.ink3, wash: color.wash });
        const data = bytes.data;
        for (let from = 0; from < data.length; from += PIECE) {
            say({ type: "chunk", data: data.slice(from, from + PIECE), last: from + PIECE >= data.length });
        }
        if (data.length === 0) setProblem("This PDF is empty.");
    }, [ready, bytes.data]);

    const onMessage = (event: WebViewMessageEvent) => {
        let said: { type?: string; pages?: number; at?: number; message?: string };
        try {
            said = JSON.parse(event.nativeEvent.data);
        } catch {
            return;
        }
        if (said.type === "ready") setReady(true);
        else if (said.type === "loaded" && typeof said.pages === "number") setPages(said.pages);
        else if (said.type === "page" && typeof said.at === "number") setAt(said.at);
        else if (said.type === "problem") setProblem(said.message ?? "This PDF could not be read.");
    };

    if (bytes.isError || problem) {
        return (
            <View style={{ padding: 24, gap: 6 }}>
                <Text size={16}>{problem ?? "The PDF could not be loaded."}</Text>
                <Text size={14} tone={color.ink3}>Save it to the phone or open it in another app from ⋯.</Text>
            </View>
        );
    }
    return (
        <View style={{ flex: 1, backgroundColor: color.wash }}>
            <WebView
                ref={view}
                accessibilityLabel={name}
                originWhitelist={["*"]}
                source={{ html: PDF_PAGE, baseUrl: ORIGIN }}
                onMessage={onMessage}
                javaScriptEnabled
                domStorageEnabled={false}
                setBuiltInZoomControls
                setDisplayZoomControls={false}
                webviewDebuggingEnabled={__DEV__}
                setSupportMultipleWindows={false}
                onShouldStartLoadWithRequest={(request) => request.url === "about:blank" || request.url.startsWith(ORIGIN)}
                style={{ flex: 1, backgroundColor: color.wash }}
            />
            {pages === null ? (
                <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: color.wash }}>
                    <ActivityIndicator color={color.ink3} />
                </View>
            ) : pages > 1 ? (
                <View pointerEvents="none" style={{ position: "absolute", bottom: 14, alignSelf: "center", paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: color.veil, borderWidth: 1, borderColor: color.line2 }}>
                    <Num size={13} tone={color.ink2}>{at} of {pages}</Num>
                </View>
            ) : null}
        </View>
    );
}
