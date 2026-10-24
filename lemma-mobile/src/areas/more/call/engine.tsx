import { forwardRef, useImperativeHandle, useRef } from "react";
import { View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { HTML as CALL_PAGE } from "./call-page.generated";
import { parseFromPage, scriptFor, type FromPage, type ToPage } from "./model";

/** The call's sound: the page built from `webview/src/call`, which runs the
 *  web's own voice clients and holds the microphone, the speaker and the
 *  voice server's socket, so audio never crosses into React Native.
 *
 *  Loaded with the web server as its base, so the clients' own
 *  `new URL("/api/live", location.href)` reaches that server's gateway, and
 *  the page is a secure context on https (or on localhost). The gateway takes
 *  no session, and nothing of the phone's is handed over: what goes in is
 *  the instructions, the words to say and the switches. Invisible, and
 *  mounted only while a call is connecting or on. */

export interface EngineHandle {
    post(message: ToPage): void;
}

export const CallEngine = forwardRef<EngineHandle, { web: string; onMessage: (message: FromPage) => void }>(function CallEngine({ web, onMessage }, ref) {
    const view = useRef<WebView>(null);
    const base = web.replace(/\/+$/, "") + "/";
    useImperativeHandle(ref, () => ({ post: (message) => view.current?.injectJavaScript(scriptFor(message)) }), []);
    const heard = (event: WebViewMessageEvent) => {
        const message = parseFromPage(event.nativeEvent.data);
        if (message) onMessage(message);
    };
    const gone = () => onMessage({ type: "error", message: "The call’s sound stopped on this phone. Try calling again." });
    return (
        <View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ position: "absolute", left: 0, top: 0, width: 1, height: 1, opacity: 0 }}>
            <WebView
                ref={view}
                source={{ html: CALL_PAGE, baseUrl: base }}
                originWhitelist={["*"]}
                onMessage={heard}
                javaScriptEnabled
                domStorageEnabled={false}
                mediaPlaybackRequiresUserAction={false}
                allowsInlineMediaPlayback
                mediaCapturePermissionGrantType="grant"
                setSupportMultipleWindows={false}
                webviewDebuggingEnabled={__DEV__}
                /* The page is the only thing this view may hold: the web site
                   itself never loads here. */
                onShouldStartLoadWithRequest={(request) => request.url === "about:blank" || request.url === base || request.url === base.slice(0, -1)}
                onRenderProcessGone={gone}
                onContentProcessDidTerminate={gone}
                style={{ width: 1, height: 1, backgroundColor: "transparent" }}
            />
        </View>
    );
});
