import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Linking, View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { useQuery } from "@tanstack/react-query";
import { lemma } from "../session/client";
import { useSession } from "../session/session";
import { Text } from "./text";
import { useTheme } from "./theme";
import { useWidgetFont } from "./widget-font";
import { color, type Palette, type Scheme } from "./tokens";

/** Web content a Lemma made, drawn inside the app: a widget in a chat today,
 *  and anything else served from the API's origin that speaks the same
 *  messages.
 *
 *  The web frames a widget in an iframe and talks to it with three messages
 *  (`lemma-backend/app/core/html_document.py`, the web's `embed-preview.tsx`):
 *  its height going up, the theme coming down, and a request to fill the
 *  composer. A WebView loading the widget itself would be its top window, and
 *  a widget only reports to its parent, so the WebView loads a small page on
 *  the API's origin holding the iframe, and that page relays the messages to
 *  the app and back. The widget's own calls for live data ride the WebView's
 *  cookie session, which signing in left behind: the same session the web
 *  uses, and no token of the app's is ever handed to a page. */

const MIN_HEIGHT = 64;
const MAX_HEIGHT = 2400;
/** A widget that has said nothing about its height by now gets this much. */
const UNREPORTED_HEIGHT = 480;
const UNREPORTED_MS = 2500;
const LOAD_DEADLINE_MS = 12_000;

/** Status colours the web hands a widget; the app's own palette has only
 *  violet and amber, so they live here (the web's `tokens.css`). */
const STATUS: Record<Scheme, { ok: string; wait: string; bad: string; info: string; dangerSoft: string; shadow: string }> = {
    light: { ok: "#2f7d57", wait: "#b5811f", bad: "#b3261e", info: "#0169cc", dangerSoft: "#fef2f2", shadow: "0 0 0 1px rgb(0 0 0 / 0.07)" },
    dark: { ok: "#6ecf9c", wait: "#dcb06a", bad: "#f08a7e", info: "#48aaff", dangerSoft: "#331919", shadow: "0 0 0 1px rgb(255 255 255 / 0.08)" },
};

/** The web's `widget-theme.ts`, from this app's palette: the widget's own
 *  vocabulary (`surface`, `muted`, `border`), never this app's names. */
export function widgetTheme(scheme: Scheme, palette: Palette, prefix: "lemma-widget" | "lemma-app"): Record<string, string> {
    const status = STATUS[scheme];
    const named: Record<string, string> = {
        "bg": palette.paper,
        "surface": palette.paper,
        "subtle": palette.chrome,
        "raised": palette.wash,
        "text": palette.ink,
        "muted": palette.ink2,
        "faint": palette.ink3,
        "border": palette.line,
        "border-strong": palette.line2,
        "shadow-rest": status.shadow,
        "shadow-raise": status.shadow,
        "inset": "1px",
        "accent": palette.ink,
        "accent-hover": palette.ink,
        "accent-soft": palette.faint,
        "success": status.ok,
        "warning": status.wait,
        "danger": status.bad,
        "info": status.info,
        "chart-1": palette.ink,
        "chart-2": status.info,
        "chart-3": status.ok,
        "chart-4": status.wait,
        "chart-5": palette.ink3,
        "radius-sm": "8px",
        "radius-md": "12px",
        "radius-panel": "24px",
        [prefix === "lemma-widget" ? "radius" : "radius-lg"]: "16px",
        "font": "\"Albert Sans\", -apple-system, system-ui, sans-serif",
        "color-scheme": scheme,
        "danger-soft": status.dangerSoft,
    };
    return Object.fromEntries(Object.entries(named).map(([name, value]) => [`--${prefix}-${name}`, value]));
}

function shellHtml(src: string, scheme: Scheme, palette: Palette, fontCss: string): string {
    const theme = JSON.stringify({ theme: scheme, widget: widgetTheme(scheme, palette, "lemma-widget"), app: widgetTheme(scheme, palette, "lemma-app") });
    return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;padding:0;background:${palette.paper}}iframe{display:block;width:100%;height:${UNREPORTED_HEIGHT}px;border:0}</style></head>
<body><iframe id="w" src=${JSON.stringify(src)} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" allow="clipboard-read; clipboard-write; fullscreen" referrerpolicy="strict-origin-when-cross-origin"></iframe>
<script>(function(){
var frame=document.getElementById("w"),theme=${theme},face=${JSON.stringify(fontCss)},origin;
try{origin=new URL(frame.src).origin}catch(e){origin="*"}
function up(m){window.ReactNativeWebView&&window.ReactNativeWebView.postMessage(JSON.stringify(m))}
function themeDown(){try{frame.contentWindow.postMessage({type:"lemma-widget-theme",theme:theme.theme,tokens:theme.widget},origin);frame.contentWindow.postMessage({type:"lemma-app-theme",theme:theme.theme,tokens:theme.app},origin)}catch(e){}}
function faceIn(){if(!face)return;try{var d=frame.contentDocument;if(!d||!d.head||d.getElementById("lemma-app-face"))return;var t=d.createElement("style");t.id="lemma-app-face";t.textContent=face;d.head.appendChild(t)}catch(e){}}
frame.addEventListener("load",function(){faceIn();themeDown();up({type:"loaded"})});
window.addEventListener("message",function(e){
 if(e.source!==frame.contentWindow)return;var d=e.data||{};
 if(d.type==="lemma-widget-height"&&typeof d.height==="number"){frame.style.height=d.height+"px";up({type:"height",height:d.height})}
 else if(d.type==="lemma-compose"&&typeof d.text==="string"){up({type:"compose",id:d.id,text:d.text,newConversation:!!d.newConversation});e.source.postMessage({type:"lemma-compose-result",id:d.id,ok:true},e.origin)}
});
})();</script></body></html>`;
}

/** A page on the API's origin that speaks the widget messages, framed. */
export function EmbedFrame({ src, onCompose, label }: {
    /** On the API's origin, signed where it needs to be. */
    src: string;
    /** The widget asked to fill the composer; the host decides what to do. */
    onCompose?: (text: string, newConversation: boolean) => void;
    label: string;
}) {
    const { server } = useSession();
    const { scheme } = useTheme();
    /* The app's face, put into the page it frames (`widget-font.ts`): read
       once, and waited for, so the page is not drawn twice. */
    const face = useWidgetFont();
    const html = useMemo(() => (face === null ? null : shellHtml(src, scheme, color, face)), [src, scheme, face]);
    const [height, setHeight] = useState<number | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [late, setLate] = useState(false);
    useEffect(() => {
        const unreported = setTimeout(() => setHeight((was) => was ?? UNREPORTED_HEIGHT), UNREPORTED_MS);
        const deadline = setTimeout(() => setLate(true), LOAD_DEADLINE_MS);
        return () => { clearTimeout(unreported); clearTimeout(deadline); };
    }, [src]);
    const onMessage = (event: WebViewMessageEvent) => {
        let said: { type?: string; height?: number; text?: string; newConversation?: boolean };
        try {
            said = JSON.parse(event.nativeEvent.data);
        } catch {
            return;
        }
        if (said.type === "loaded") setLoaded(true);
        else if (said.type === "height" && typeof said.height === "number") setHeight(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(said.height))));
        else if (said.type === "compose" && typeof said.text === "string") onCompose?.(said.text, Boolean(said.newConversation));
    };
    if (late && !loaded) return <Text size={14} tone={color.ink3} style={{ paddingVertical: 8 }}>{label} did not load.</Text>;
    if (html === null) {
        return (
            <View style={{ height: 160, alignItems: "center", justifyContent: "center" }}>
                <ActivityIndicator color={color.ink3} />
            </View>
        );
    }
    return (
        <View accessibilityLabel={label} style={{ height: height ?? 160, borderRadius: 14, overflow: "hidden", backgroundColor: color.paper }}>
            <WebView
                originWhitelist={["*"]}
                source={{ html, baseUrl: server.api + "/" }}
                onMessage={onMessage}
                scrollEnabled={false}
                nestedScrollEnabled={false}
                sharedCookiesEnabled
                thirdPartyCookiesEnabled
                domStorageEnabled
                javaScriptEnabled
                webviewDebuggingEnabled={__DEV__}
                setSupportMultipleWindows={false}
                style={{ backgroundColor: "transparent" }}
                onShouldStartLoadWithRequest={(request) => {
                    /* The shell and the widget load here; a link the widget
                       follows out of itself opens in the browser. */
                    const own = request.url === "about:blank" || request.url.startsWith(server.api) || request.isTopFrame === false;
                    if (!own) void Linking.openURL(request.url);
                    return own;
                }}
            />
            {!loaded ? (
                <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: color.paper }}>
                    <ActivityIndicator color={color.ink3} />
                </View>
            ) : null}
        </View>
    );
}

/** A widget a Lemma drew in a chat, live: its signed address is minted for
 *  this view, as the web does, and lasts half an hour. With no server behind
 *  the session (sample data), its `fallback` text stands in. */
export function WidgetFrame({ podId, conversationId, toolCallId, title, fallback, onCompose }: {
    podId: string;
    conversationId: string;
    toolCallId: string;
    title: string;
    fallback?: string | null;
    onCompose?: (text: string, newConversation: boolean) => void;
}) {
    const { mode, server } = useSession();
    const live = mode === "live";
    const embed = useQuery({
        queryKey: [server.id, "widget-embed", podId, conversationId, toolCallId],
        queryFn: async () => (await lemma(server, podId).widgets.embedUrl({ conversation_id: conversationId, tool_call_id: toolCallId })).url,
        enabled: live,
        staleTime: 25 * 60_000,
        gcTime: 25 * 60_000,
    });
    if (!live || embed.isError) {
        return (
            <View style={{ borderWidth: 1, borderColor: color.line2, borderRadius: 14, padding: 14, gap: 4 }}>
                <Text size={15} weight="medium">{title}</Text>
                {fallback ? <Text size={14} tone={color.ink2}>{fallback}</Text> : null}
                {embed.isError ? <Text size={13} tone={color.ink3}>It could not be drawn just now.</Text> : null}
            </View>
        );
    }
    if (!embed.data) {
        return (
            <View style={{ height: 160, alignItems: "center", justifyContent: "center" }}>
                <ActivityIndicator color={color.ink3} />
            </View>
        );
    }
    return <EmbedFrame src={embed.data} label={title} onCompose={onCompose} />;
}
