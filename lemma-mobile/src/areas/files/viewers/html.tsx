import { useMemo, useState } from "react";
import { ActivityIndicator, Linking, View } from "react-native";
import { WebView } from "react-native-webview";
import { useTheme } from "../../../ui/theme";
import { widgetTheme } from "../../../ui/web-content";
import { color } from "../../../ui/tokens";

/** An HTML file, as the web shows one on its stage (`file-view.tsx`'s
 *  `PageFrame`): the page's own bytes in a sandboxed frame, scripts allowed
 *  and nothing else. The frame has no origin of its own, so it can reach no
 *  session, cookie or storage of the app's, and it cannot navigate the screen
 *  away. The app's theme goes in as the widget variables a Lemma's pages are
 *  written against, so a page follows the app into dark. */

/** The shell's own origin: never the API's, never the web app's. */
const ORIGIN = "https://page.lemma.invalid/";

function framed(html: string, theme: Record<string, string>, scheme: string): string {
    const style = `<style>:root{${Object.entries(theme).map(([name, value]) => `${name}:${value}`).join(";")};color-scheme:${scheme}}</style>`;
    const inner = style + html;
    const escaped = inner.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
    return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;background:${theme["--lemma-widget-bg"] ?? "transparent"}}iframe{display:block;width:100%;height:100%;border:0}</style></head>
<body><iframe sandbox="allow-scripts" srcdoc="${escaped}"></iframe></body></html>`;
}

export function HtmlView({ html, name }: { html: string; name: string }) {
    const { scheme } = useTheme();
    const [loading, setLoading] = useState(true);
    const page = useMemo(() => framed(html, widgetTheme(scheme, color, "lemma-widget"), scheme), [html, scheme]);
    return (
        <View style={{ flex: 1, backgroundColor: color.paper }}>
            <WebView
                accessibilityLabel={name}
                originWhitelist={["*"]}
                source={{ html: page, baseUrl: ORIGIN }}
                javaScriptEnabled
                domStorageEnabled={false}
                setSupportMultipleWindows={false}
                webviewDebuggingEnabled={__DEV__}
                onLoadEnd={() => setLoading(false)}
                onShouldStartLoadWithRequest={(request) => {
                    /* The shell and its frame load here; a link out of the page
                       opens in the browser rather than inside the app. */
                    const own = request.url === "about:blank" || request.url === "about:srcdoc" || request.url.startsWith(ORIGIN);
                    if (!own && request.isTopFrame !== false && /^https?:/.test(request.url)) void Linking.openURL(request.url);
                    return own;
                }}
                style={{ flex: 1, backgroundColor: color.paper }}
            />
            {loading ? (
                <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: color.paper }}>
                    <ActivityIndicator color={color.ink3} />
                </View>
            ) : null}
        </View>
    );
}
