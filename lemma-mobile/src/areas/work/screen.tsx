import { useState, type ReactNode } from "react";
import { KeyboardAvoidingView, Platform, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { routes } from "../../data/routes";
import { BackLink } from "../../ui/chrome";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";

/** A work screen's frame: a back link that names where it goes, a header,
 *  and the rest scrolling under it, pulled down to read again. */
export function Screen({ podId, back, right, onRefresh, children, foot }: {
    podId: string;
    /** What the back link names; it goes back, or to the Lemma's page when
     *  there is nothing to go back to. */
    back: string;
    right?: ReactNode;
    /** Read again; the spinner shows while this one, pulled by hand, is out.
     *  A read the screen makes by itself draws nothing. */
    onRefresh?: () => Promise<unknown>;
    children: ReactNode;
    /** Fixed under the scroll: a box, a save bar. */
    foot?: ReactNode;
}) {
    const router = useRouter();
    const [pulling, setPulling] = useState(false);
    const pull = () => {
        if (!onRefresh) return;
        setPulling(true);
        void onRefresh().finally(() => setPulling(false));
    };
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                    <BackLink label={back} onPress={() => (router.canGoBack() ? router.back() : router.replace(routes.lemma(podId) as never))} />
                    <View style={{ flex: 1 }} />
                    {right}
                </View>
                <ScrollView
                    keyboardShouldPersistTaps="handled"
                    contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 32 }}
                    refreshControl={onRefresh ? <RefreshControl refreshing={pulling} onRefresh={pull} tintColor={color.ink3} colors={[color.ink2]} progressBackgroundColor={color.paper} /> : undefined}
                >
                    {children}
                </ScrollView>
                {foot}
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

/** The title block: a glyph, the name, what it is for, its tags. */
export function Title({ glyph, title, small, children }: { glyph?: ReactNode; title: string; small?: ReactNode; children?: ReactNode }) {
    return (
        <View style={{ gap: 8, paddingTop: 6, paddingHorizontal: 4, paddingBottom: 4 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                {glyph}
                <Text accessibilityRole="header" size={24} weight="medium" style={{ flex: 1, letterSpacing: -0.24, lineHeight: 29 }}>
                    {title}
                    {small ? <Text size={15} tone={color.ink3}>{"  "}{small}</Text> : null}
                </Text>
            </View>
            {children}
        </View>
    );
}
