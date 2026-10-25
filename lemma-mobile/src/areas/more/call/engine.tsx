import { forwardRef, useImperativeHandle, useRef } from "react";
import { View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { HTML as CALL_PAGE } from "./call-page.generated";
import { parseFromPage, scriptFor, type FromPage, type ToPage } from "./model";

/** The address the page is given: any secure one will do, since it loads
 *  nothing and reaches nothing. A secure context is what lets it ask for the
 *  microphone, whatever server the app is signed in to. */
const BASE = "https://localhost/";

/** The call's sound: the page built from `webview/src/call`, which holds the
 *  microphone and the speaker. PCM crosses the bridge as base64; the socket
 *  and the session stay in React Native. Invisible, and mounted only while a
 *  call is connecting or on. */

export interface EngineHandle {
    post(message: ToPage): void;
}

export const CallEngine = forwardRef<EngineHandle, { onMessage: (message: FromPage) => void }>(function CallEngine({ onMessage }, ref) {
    const view = useRef<WebView>(null);
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
                source={{ html: CALL_PAGE, baseUrl: BASE }}
                originWhitelist={["*"]}
                onMessage={heard}
                javaScriptEnabled
                domStorageEnabled={false}
                mediaPlaybackRequiresUserAction={false}
                allowsInlineMediaPlayback
                mediaCapturePermissionGrantType="grant"
                setSupportMultipleWindows={false}
                webviewDebuggingEnabled={__DEV__}
                /* The page is the only thing this view may hold. */
                onShouldStartLoadWithRequest={(request) => request.url === "about:blank" || request.url === BASE || request.url === BASE.slice(0, -1)}
                onRenderProcessGone={gone}
                onContentProcessDidTerminate={gone}
                style={{ width: 1, height: 1, backgroundColor: "transparent" }}
            />
        </View>
    );
});
