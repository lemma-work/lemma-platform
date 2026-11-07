import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ActivityIndicator, AppState, Linking, View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { useFocusEffect } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import { useSession } from "../../session/session";
import { useTheme } from "../../ui/theme";
import { color } from "../../ui/tokens";
import { useFilesKey, useFilesSource } from "./hooks";
import type { FileContent } from "./source";
import { HTML as EDITOR_PAGE } from "./web/editor-page.generated";
import type { Anchor, CallMethod, CarriedBlob, FromPage, HighlightAnchor, ToPage } from "./web/protocol";
import { renamePage } from "@web/docpages/rename";

/** A page, in the web's own editor: TipTap, its slash menu, live table
 *  views, widgets, images and sub-pages, built into one HTML page
 *  (`lemma-mobile/webview`) and run here in a WebView.
 *
 *  The page reaches the network only through this component. Each `call` it
 *  makes is answered from the files source, with the app's session; the
 *  page never holds a token, a cookie of the app's, or an address it could
 *  use to reach the API itself. */

/** The page's own origin: never the API's or the web app's. */
const ORIGIN = "https://page-editor.lemma.invalid/";

export interface PageEditorHandle {
    /** Hand over what is typed before the screen goes. */
    flush: () => Promise<void>;
    blur: () => void;
    retry: () => void;
}

export interface PageEvents {
    onStatus: (label: string | null, bad: boolean) => void;
    onFocus: (focused: boolean) => void;
    onOpenFile: (path: string) => void;
    onOpenTable: (name: string) => void;
    onAsk: (text: string) => void;
    onCommentStart: (anchor: Anchor | null) => void;
    onCommentFocus: (id: string | null) => void;
    onRenamed: (path: string) => void;
}

const statusOf = (error: unknown): number | null => {
    const said = (error as { statusCode?: unknown } | null)?.statusCode;
    return typeof said === "number" ? said : null;
};

export const PageEditor = forwardRef<PageEditorHandle, PageEvents & {
    podId: string;
    path: string;
    text: string;
    lemmaName: string;
    personal: boolean;
    comments: { anchors: HighlightAnchor[]; active: string | null; enabled: boolean };
}>(function PageEditor(props, ref) {
    const { podId, lemmaName, personal, comments } = props;
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const { mode } = useSession();
    const { scheme } = useTheme();
    const view = useRef<WebView>(null);
    const [ready, setReady] = useState(false);
    const [started, setStarted] = useState(false);
    const events = useRef<PageEvents>(props);
    events.current = props;
    const flushed = useRef<(() => void) | null>(null);
    /* Read once: the editor takes its text when it is built and follows the
       file itself after that. */
    const opening = useRef({ path: props.path, text: props.text });

    const say = useCallback((message: ToPage) => {
        view.current?.injectJavaScript(`window.__fromApp && window.__fromApp(${JSON.stringify(message)});true;`);
    }, []);

    const theme = useCallback(() => ({ scheme, palette: { ...color } as Record<string, string> }), [scheme]);

    useImperativeHandle(ref, () => ({
        flush: () => new Promise<void>((done) => {
            if (!started) { done(); return; }
            const timer = setTimeout(() => { flushed.current = null; done(); }, 2500);
            flushed.current = () => { clearTimeout(timer); flushed.current = null; done(); };
            say({ type: "flush" });
        }),
        blur: () => say({ type: "blur" }),
        retry: () => say({ type: "retry" }),
    }), [say, started]);

    /* Start the page once it is listening. */
    useEffect(() => {
        if (!ready || started) return;
        setStarted(true);
        say({ type: "init", podId, path: opening.current.path, text: opening.current.text, botName: lemmaName, sample: mode === "sample", personal, theme: theme() });
    }, [ready, started, say, podId, lemmaName, mode, personal, theme]);

    useEffect(() => {
        if (started) say({ type: "theme", ...theme() });
    }, [started, theme, say]);

    useEffect(() => {
        if (started) say({ type: "comments", ...comments });
    }, [started, comments, say]);

    /* On screen means this screen in front and the app in the foreground:
       the page stops asking whether its file changed when it is not. */
    useFocusEffect(useCallback(() => {
        if (started) say({ type: "visible", visible: true });
        return () => { if (started) say({ type: "visible", visible: false }); };
    }, [started, say]));
    useEffect(() => {
        const listening = AppState.addEventListener("change", (state) => { if (started) say({ type: "visible", visible: state === "active" }); });
        return () => listening.remove();
    }, [started, say]);

    async function answer(method: CallMethod, args: unknown[]): Promise<unknown> {
        switch (method) {
            case "readFile":
                return source.read(podId, String(args[1]));
            case "writeFile": {
                const path = String(args[1]);
                const text = String(args[2]);
                await source.writeText(podId, path, text);
                cache.setQueryData<FileContent>(keyOf("read", path), (was) => (was ? { ...was, text, size: text.length } : was));
                void cache.invalidateQueries({ queryKey: keyOf("list") });
                return null;
            }
            case "runQuery":
                return source.runQuery(podId, String(args[1]));
            case "tableRows":
                return source.tableRows(podId, String(args[1]));
            case "listLibrary":
                if (args[1] === "tables") return { items: (await source.tableNames(podId)).map((name) => ({ id: name, name, kind: "table", path: name, updated: "", detail: "" })), next: null };
                return source.list(podId, String(args[2] ?? "/"));
            case "tableColumns":
                return source.tableColumns(podId, String(args[1]));
            case "fileDetail": {
                const stamp = await source.stamp(podId, String(args[0]));
                return { content_sha256: stamp.sha, updated_at: stamp.updatedAt };
            }
            case "fileUrl":
                return { url: await source.signedUrl(podId, String(args[0])) };
            case "upload": {
                const file = args[0] as CarriedBlob;
                const options = (args[1] ?? {}) as { name?: string; directoryPath?: string; searchEnabled?: boolean };
                const made = await source.uploadBytes(podId, options.directoryPath ?? "/", { base64: file.__blob, name: options.name ?? file.name ?? "file", type: file.type || "application/octet-stream" }, options.searchEnabled ?? true);
                return { path: made.path, name: made.name };
            }
            case "renamePage": {
                const from = String(args[0]);
                const to = await renamePage({
                    move: (a, b) => source.move(podId, a, b),
                    read: async (at) => {
                        try {
                            return (await source.read(podId, at)).text ?? null;
                        } catch (error) {
                            if (statusOf(error) === 404) return null;
                            throw error;
                        }
                    },
                    write: (at, text) => source.writeText(podId, at, text),
                    moveComments: (a, b) => source.moveComments(podId, a, b),
                }, from, String(args[1]));
                if (to !== from) {
                    await source.movePageConversation(podId, from, to).catch(() => undefined);
                    void cache.invalidateQueries({ queryKey: keyOf("list") });
                    events.current.onRenamed(to);
                }
                return to;
            }
        }
    }

    const onMessage = (event: WebViewMessageEvent) => {
        let said: FromPage;
        try {
            said = JSON.parse(event.nativeEvent.data) as FromPage;
        } catch {
            return;
        }
        const on = events.current;
        switch (said.type) {
            case "ready": setReady(true); return;
            case "call": {
                const { id, method, args } = said;
                answer(method, args).then(
                    (value) => say({ type: "reply", id, ok: true, value: value ?? null }),
                    (error: unknown) => say({ type: "reply", id, ok: false, message: error instanceof Error ? error.message : "That did not work.", statusCode: statusOf(error) }),
                );
                return;
            }
            case "status": on.onStatus(said.label, said.bad); return;
            case "focus": on.onFocus(said.focused); return;
            case "open-file": on.onOpenFile(said.path); return;
            case "open-table": on.onOpenTable(said.name); return;
            case "ask": on.onAsk(said.text); return;
            case "comment-start": on.onCommentStart(said.anchor); return;
            case "comment-focus": on.onCommentFocus(said.id); return;
            case "flushed": flushed.current?.(); return;
            case "problem": if (__DEV__) console.warn("Page editor:", said.message); return;
        }
    };

    return (
        <View style={{ flex: 1, backgroundColor: color.paper }}>
            <WebView
                ref={view}
                accessibilityLabel="Page"
                originWhitelist={["*"]}
                source={{ html: EDITOR_PAGE, baseUrl: ORIGIN }}
                onMessage={onMessage}
                javaScriptEnabled
                domStorageEnabled
                keyboardDisplayRequiresUserAction={false}
                hideKeyboardAccessoryView={false}
                setSupportMultipleWindows={false}
                webviewDebuggingEnabled={__DEV__}
                allowFileAccess={false}
                textZoom={100}
                onRenderProcessGone={() => { setReady(false); setStarted(false); view.current?.reload(); }}
                onContentProcessDidTerminate={() => { setReady(false); setStarted(false); view.current?.reload(); }}
                onShouldStartLoadWithRequest={(request) => {
                    /* A link in the page opens where it points, never in here. */
                    if (request.url === "about:blank" || request.url.startsWith(ORIGIN) || request.url.startsWith("about:srcdoc")) return true;
                    if (request.isTopFrame !== false && /^https?:/.test(request.url)) void Linking.openURL(request.url);
                    return false;
                }}
                style={{ flex: 1, backgroundColor: color.paper }}
            />
            {!started ? (
                <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: color.paper }}>
                    <ActivityIndicator color={color.ink3} />
                </View>
            ) : null}
        </View>
    );
});
