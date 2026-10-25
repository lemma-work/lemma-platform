import { useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import * as WebBrowser from "expo-web-browser";
import { Icon } from "./icons";
import { Press } from "./press";
import { Text } from "./text";
import { color, TOUCH } from "./tokens";

/** A page of the web shown full screen: a pod app, or a workspace page the
 *  phone has no screen for yet.
 *
 *  It signs in the way a browser does, with the cookie session the sign-in
 *  WebView left (pod apps read the parent-domain SuperTokens cookie). Where
 *  that is missing, the page's own "Sign in with Lemma" runs in here. The
 *  phone's own header tokens never go in: a refresh token held in two places
 *  trips SuperTokens' theft detection and signs both out. */
export function Framed({ url, title, subtitle, children, right }: {
    url: string | null;
    title: string;
    subtitle?: string;
    children?: React.ReactNode;
    /** More in the header, before Reload: an app's Share. */
    right?: React.ReactNode;
}) {
    const router = useRouter();
    const view = useRef<WebView>(null);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState<string | null>(null);
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingTop: 2, paddingBottom: 6, paddingLeft: 4, paddingRight: 6, borderBottomWidth: 1, borderBottomColor: color.line }}>
                <Press label="Close" onPress={() => (router.canGoBack() ? router.back() : router.replace("/apps" as never))} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="back" size={22} width={1.9} />
                </Press>
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text size={16} weight="medium" numberOfLines={1}>{title}</Text>
                    {subtitle ? <Text size={12.5} tone={color.ink2} numberOfLines={1}>{subtitle}</Text> : null}
                </View>
                {right}
                {url ? (
                    <>
                        <Press label="Reload" onPress={() => { setFailed(null); view.current?.reload(); }} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="refresh" size={19} tone={color.ink2} />
                        </Press>
                        <Press label="Open in the browser" onPress={() => void WebBrowser.openBrowserAsync(url)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="forward" size={19} tone={color.ink2} />
                        </Press>
                    </>
                ) : null}
            </View>
            {url ? (
                <View style={{ flex: 1 }}>
                    <WebView
                        ref={view}
                        source={{ uri: url }}
                        originWhitelist={["http://*", "https://*"]}
                        sharedCookiesEnabled
                        thirdPartyCookiesEnabled
                        domStorageEnabled
                        javaScriptEnabled
                        allowsBackForwardNavigationGestures
                        setSupportMultipleWindows={false}
                        onLoadEnd={() => setLoading(false)}
                        onError={(event) => { setLoading(false); setFailed(event.nativeEvent.description || "This page did not load."); }}
                        style={{ flex: 1 }}
                    />
                    {loading ? (
                        <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: color.paper }}>
                            <ActivityIndicator color={color.ink3} />
                        </View>
                    ) : null}
                    {failed ? (
                        <View style={{ position: "absolute", left: 16, right: 16, top: 16, padding: 14, borderRadius: 14, backgroundColor: color.amberWash }}>
                            <Text size={14.5} tone={color.amber}>{failed}</Text>
                        </View>
                    ) : null}
                </View>
            ) : (
                <View style={{ flex: 1, padding: 24, gap: 16 }}>{children}</View>
            )}
        </SafeAreaView>
    );
}
