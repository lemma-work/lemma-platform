import { View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { routes } from "../data/routes";
import { BackLink } from "./chrome";
import { Button } from "./press";
import { Text } from "./text";
import { color } from "./tokens";

/** What the phone does not show yet, said plainly, with the web's own page
 *  one tap away, so no part of a Lemma is out of reach meanwhile. Each area
 *  replaces its placeholders as it lands (PARITY.md). `tail` is the
 *  workspace's address (`data/routes.ts` `workspace`); `podId` is "" for
 *  what belongs to the account, not a Lemma. */
export function ComingTab({ say, podId, tail, title }: { say: string; podId: string; tail: string; title: string }) {
    const router = useRouter();
    return (
        <View style={{ gap: 12, paddingHorizontal: 4, paddingVertical: 8 }}>
            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{say}</Text>
            <View style={{ flexDirection: "row" }}>
                <Button title="Open on the web" kind="outline" onPress={() => router.push(routes.web(podId, tail, title) as never)} />
            </View>
        </View>
    );
}

/** The same, as a screen of its own, for a place reached from elsewhere. */
export function ComingScreen({ title, say, podId, tail, back }: { title: string; say: string; podId: string; tail: string; back?: string }) {
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 4, paddingHorizontal: 6 }}>
                <BackLink label={back} />
            </View>
            <View style={{ paddingHorizontal: 20, paddingTop: 8, gap: 8 }}>
                <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{title}</Text>
                <View style={{ marginHorizontal: -4 }}>
                    <ComingTab say={say} podId={podId} tail={tail} title={title} />
                </View>
            </View>
        </SafeAreaView>
    );
}
