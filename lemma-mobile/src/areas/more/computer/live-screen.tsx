import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, TurboModuleRegistry, View, type TurboModule } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { freshAccess } from "../../../session/auth";
import { useSession } from "../../../session/session";
import { copyText } from "../../../thread/clipboard";
import { palettes } from "../../../ui/tokens";
import { useFitDisplay } from "./hooks";
import { displaySize, RESIZE_SETTLE_MS, screenSocketUrl } from "./model";
import { HTML as SCREEN_PAGE } from "./vnc-page.generated";
import { isSettled, retryDelay, stateFromClose, type LiveState } from "@web/computer/live";
import { base64Of, bytesOf, fromPage, toPageScript, type Glass, type ToPage } from "./wire";

/** The computer's own display, live (the web's `live-screen.tsx`).
 *
 *  React Native owns the socket: it alone can put the phone's bearer token
 *  in the handshake's `Authorization` header, which the server reads first,
 *  so the address carries no credential and the page never holds one. The
 *  WebView only draws: noVNC in `webview/src/vnc`, fed what the socket
 *  receives as base64 and handing back what it sends (`wire.ts`).
 *
 *  Attaching starts a browser if none is running and holds the computer
 *  awake for as long as the socket is open. So this mounts on a press, and
 *  its socket closes the moment nobody is looking: when it unmounts, and
 *  while the app is in the background. It must never be rendered
 *  speculatively. */

/** The page's own origin, which reaches nothing: never the API's or the web
 *  app's. https, so noVNC finds itself in a secure context. */
const PAGE_ORIGIN = "https://computer.lemma.invalid/";

/** The height of the page's bar, where it draws one (`screen.css`). */
const BAR_HEIGHT = 49;

/** The glass the picture sits on: dark in every theme, as a screen is, so
 *  it is drawn in the dark palette's tokens whichever is in use. */
export function glassTokens(): Glass {
    const dark = palettes.dark;
    return {
        ground: dark.chrome,
        ink: dark.ink,
        quiet: dark.ink2,
        line: dark.line2,
        press: dark.wash,
        font: '"Albert Sans", system-ui, sans-serif',
    };
}

/** React Native's WebSocket takes headers as a third argument, which the DOM's
 *  type does not say. */
type NativeSocket = new (url: string, protocols: string | string[] | undefined, options: { headers: Record<string, string> }) => WebSocket;

interface ReadableClipboard extends TurboModule {
    getString(): Promise<string>;
}

/** The phone's clipboard, for Paste. React Native's own module, as
 *  `thread/clipboard.ts` reaches it to write. */
async function clipboardText(): Promise<string> {
    const clipboard = TurboModuleRegistry.get<ReadableClipboard>("Clipboard");
    try {
        return (await clipboard?.getString()) ?? "";
    } catch {
        return "";
    }
}

function useAppActive(): boolean {
    const [active, setActive] = useState(AppState.currentState !== "background");
    useEffect(() => {
        const subscription = AppState.addEventListener("change", (state) => setActive(state !== "background"));
        return () => subscription.remove();
    }, []);
    return active;
}

export function LiveScreen({ origin = null, conversationId = null, reopen = 0, bar, fit = true, onState, onDone }: {
    /** A site to steer the browser to: a sign-in. Re-steered on every
     *  connect, so `reopen` is how a late answer sends it there again. */
    origin?: string | null;
    /** The chat whose browser to show, when not a sign-in. */
    conversationId?: string | null;
    /** Bumped to drop this socket and open another. */
    reopen?: number;
    /** The thin bar the page draws under the picture: Keyboard and Paste,
     *  and Done when there is somewhere to go back to. Null for none. */
    bar: { done: boolean } | null;
    /** Whether this screen may reshape the computer's display to its box. */
    fit?: boolean;
    onState: (state: LiveState) => void;
    onDone?: () => void;
}) {
    const { server } = useSession();
    const view = useRef<WebView>(null);
    const [ready, setReady] = useState(false);
    const [connected, setConnected] = useState(false);
    const [box, setBox] = useState<{ width: number; height: number } | null>(null);
    const active = useAppActive();
    const report = useRef(onState);
    report.current = onState;
    const done = useRef(onDone);
    done.current = onDone;
    /* The open socket's two hands, for what the page asks of it. */
    const sendBytes = useRef<(base64: string) => void>(() => undefined);
    const closeSocket = useRef<() => void>(() => undefined);
    const attempt = useRef(0);

    const post = useCallback((message: ToPage) => {
        view.current?.injectJavaScript(toPageScript(message));
    }, []);

    const barDone = bar ? bar.done : null;
    useEffect(() => {
        if (!ready) return;
        post({ type: "chrome", bar: barDone === null ? null : { done: barDone }, glass: glassTokens() });
    }, [ready, barDone, post]);

    /* The socket, one attempt at a time, until it closes in a way trying
       again cannot fix. */
    useEffect(() => {
        if (!ready || !active) return;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let socket: WebSocket | null = null;
        let queue: string[] = [];
        let flushing = false;

        /* Many small messages arrive together; the page gets them in one
           hand-over per turn, in order. */
        const flush = () => {
            flushing = false;
            if (!queue.length) return;
            const data = queue;
            queue = [];
            post({ type: "frames", data });
        };

        const connect = async () => {
            if (stopped) return;
            report.current("connecting");
            const token = await freshAccess();
            if (stopped) return;
            if (!token) {
                report.current("signed-out");
                return;
            }
            post({ type: "attach", viewOnly: false });
            const Socket = WebSocket as unknown as NativeSocket;
            const made = new Socket(screenSocketUrl(server.api, { origin, conversationId }), undefined, { headers: { Authorization: `Bearer ${token}` } });
            made.binaryType = "arraybuffer";
            socket = made;
            made.onopen = () => {
                if (socket !== made) return;
                post({ type: "open", protocol: made.protocol ?? "" });
            };
            made.onmessage = (event: MessageEvent) => {
                if (socket !== made) return;
                const data: unknown = event.data;
                if (data instanceof ArrayBuffer) queue.push(base64Of(new Uint8Array(data)));
                else if (ArrayBuffer.isView(data)) queue.push(base64Of(new Uint8Array(data.buffer, data.byteOffset, data.byteLength)));
                else return;
                if (!flushing) {
                    flushing = true;
                    setTimeout(flush, 0);
                }
            };
            made.onerror = () => undefined;
            made.onclose = (event: CloseEvent) => {
                if (socket !== made) return;
                flush();
                socket = null;
                const code = typeof event.code === "number" ? event.code : 1006;
                post({ type: "closed", code, reason: event.reason ?? "" });
                if (stopped) return;
                setConnected(false);
                const state = stateFromClose(code);
                report.current(state);
                if (!isSettled(state)) timer = setTimeout(() => void connect(), retryDelay(attempt.current++));
            };
        };

        sendBytes.current = (base64) => {
            if (socket && socket.readyState === 1) socket.send(bytesOf(base64));
        };
        closeSocket.current = () => {
            try {
                socket?.close();
            } catch {
                /* Already closing. */
            }
        };

        void connect();
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
            const open = socket;
            socket = null;
            queue = [];
            sendBytes.current = () => undefined;
            closeSocket.current = () => undefined;
            setConnected(false);
            post({ type: "detach" });
            /* The point of letting go: the socket is what holds the computer
               awake. */
            try {
                open?.close();
            } catch {
                /* Already gone. */
            }
        };
    }, [ready, active, server.api, origin, conversationId, reopen, post]);

    /* Fit the display to the box the picture is drawn in, on every new
       connection and after a size change settles, so a site serves its
       phone layout. */
    const fitDisplay = useFitDisplay();
    const askSize = useRef(fitDisplay.mutate);
    askSize.current = fitDisplay.mutate;
    const asked = useRef("");
    const pictureHeight = box ? box.height - (bar ? BAR_HEIGHT : 0) : 0;
    useEffect(() => {
        if (!connected) {
            asked.current = "";
            return;
        }
        if (!fit || !box) return;
        const wanted = displaySize({ width: box.width, height: pictureHeight });
        const said = `${wanted.width}x${wanted.height}`;
        if (said === asked.current) return;
        const timer = setTimeout(() => {
            asked.current = said;
            askSize.current(wanted, { onError: () => { asked.current = ""; } });
        }, asked.current ? RESIZE_SETTLE_MS : 0);
        return () => clearTimeout(timer);
    }, [fit, connected, box, pictureHeight]);

    const onMessage = (event: WebViewMessageEvent) => {
        const said = fromPage(event.nativeEvent.data);
        if (!said) return;
        switch (said.type) {
            case "ready":
                setReady(true);
                return;
            case "send":
                sendBytes.current(said.data);
                return;
            case "close":
            case "problem":
                closeSocket.current();
                return;
            case "connected":
                attempt.current = 0;
                setConnected(true);
                report.current("live");
                return;
            case "clipboard":
                copyText(said.text);
                return;
            case "paste-request":
                void clipboardText().then((text) => {
                    if (text) post({ type: "paste", text });
                });
                return;
            case "done":
                done.current?.();
                return;
            case "disconnected":
                /* The socket's own close says why, and decides what next. */
                return;
        }
    };

    const glass = glassTokens();
    return (
        <View style={{ flex: 1, backgroundColor: glass.ground }} onLayout={(event) => setBox({ width: event.nativeEvent.layout.width, height: event.nativeEvent.layout.height })}>
            <WebView
                ref={view}
                accessibilityLabel="The computer’s screen"
                source={{ html: SCREEN_PAGE, baseUrl: PAGE_ORIGIN }}
                originWhitelist={["*"]}
                onMessage={onMessage}
                onShouldStartLoadWithRequest={(request) => request.url === "about:blank" || request.url.startsWith(PAGE_ORIGIN)}
                javaScriptEnabled
                domStorageEnabled={false}
                incognito
                cacheEnabled={false}
                setSupportMultipleWindows={false}
                scrollEnabled={false}
                bounces={false}
                overScrollMode="never"
                textZoom={100}
                setBuiltInZoomControls={false}
                hideKeyboardAccessoryView
                webviewDebuggingEnabled={__DEV__}
                onContentProcessDidTerminate={() => { setReady(false); view.current?.reload(); }}
                onRenderProcessGone={() => { setReady(false); view.current?.reload(); }}
                style={{ flex: 1, backgroundColor: glass.ground }}
            />
        </View>
    );
}
