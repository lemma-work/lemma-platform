import { ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback } from "react";
import { SafeAreaView } from "react-native-safe-area-context";
import { setStatusBarStyle } from "expo-status-bar";
import { LemmaMark } from "../../ui/icons";
import { Press } from "../../ui/press";
import { Counted, Text } from "../../ui/text";
import { TOUCH } from "../../ui/tokens";

/** What a notification with actions would look like: the canvas's second
 *  artboard, drawn on a lock screen. A mock only -- the backend sends no
 *  push yet, so nothing here is wired to anything. */

const LOCK = { ground: "#19191c", card: "#2c2c31", button: "#3a3a40", ink: "#ececec", soft: "#dcdcdc", quiet: "#b4b4b4", rule: "rgba(255,255,255,0.08)" };

function Header({ when }: { when: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <View style={{ width: 22, height: 22, borderRadius: 6, backgroundColor: "#ffffff", alignItems: "center", justifyContent: "center" }}><LemmaMark size={15} /></View>
            <Text size={13} tone={LOCK.quiet}>Lemma</Text>
            <View style={{ flex: 1 }} />
            <Text size={13} tone={LOCK.quiet}>{when}</Text>
        </View>
    );
}

export default function Notices() {
    const router = useRouter();
    /* Light on the dark lock screen while it is in front, and dark again the
       moment it is not: a mounted <StatusBar> kept its style from the back
       stack. */
    useFocusEffect(useCallback(() => {
        setStatusBarStyle("light");
        return () => setStatusBarStyle("dark");
    }, []));
    const row = (title: string, last = false) => (
        <Press key={title} label={`${title}, in the preview only`} onPress={() => undefined} style={{ height: 48, justifyContent: "center", paddingHorizontal: 16, borderBottomWidth: last ? 0 : 1, borderBottomColor: LOCK.rule }}>
            <Text size={16} tone={LOCK.ink}>{title}</Text>
        </Press>
    );
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: LOCK.ground }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 8 }}>
                <Press label="Close the preview" onPress={() => router.back()} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                    <Text size={16} tone={LOCK.ink}>Close</Text>
                </Press>
                <View style={{ flex: 1, alignItems: "center" }}><Text size={13} tone={LOCK.quiet}>A preview: nothing sends these yet</Text></View>
                <View style={{ width: 64 }} />
            </View>
            <ScrollView contentContainerStyle={{ paddingTop: 90, paddingHorizontal: 12, paddingBottom: 40, gap: 10 }}>
                <View style={{ backgroundColor: LOCK.card, borderRadius: 22, overflow: "hidden" }}>
                    <View style={{ paddingTop: 14, paddingHorizontal: 16, paddingBottom: 12, gap: 6 }}>
                        <Header when="now" />
                        <Text size={15.5} weight="medium" tone={LOCK.ink} style={{ marginTop: 2 }}>Support needs your yes</Text>
                        <Counted size={15} tone={LOCK.soft} style={{ lineHeight: 21 }}>Refund $240 to Northwind? Their order arrived broken. Approve and they hear back today.</Counted>
                    </View>
                    <View style={{ borderTopWidth: 1, borderTopColor: LOCK.rule }}>
                        {row("Approve")}
                        {row("Not this one")}
                        {row("Open the chat", true)}
                    </View>
                </View>
                <View style={{ backgroundColor: LOCK.card, borderRadius: 22, paddingVertical: 14, paddingHorizontal: 16, gap: 8 }}>
                    <Header when="2h ago" />
                    <Text size={15.5} weight="medium" tone={LOCK.ink}>Launch asks</Text>
                    <Text size={15} tone={LOCK.soft}>Which day should the launch post go out?</Text>
                    <View style={{ flexDirection: "row", gap: 8, marginTop: 2 }}>
                        {["Mon 10", "Tue 11", "Wed 12"].map((day) => (
                            <Press key={day} label={`${day}, in the preview only`} onPress={() => undefined} style={{ flex: 1, height: 44, borderRadius: 12, backgroundColor: LOCK.button, alignItems: "center", justifyContent: "center" }}>
                                <Counted size={15} tone={LOCK.ink}>{day}</Counted>
                            </Press>
                        ))}
                    </View>
                </View>
                <View style={{ backgroundColor: LOCK.card, borderRadius: 22, paddingVertical: 14, paddingHorizontal: 16, gap: 6 }}>
                    <Header when="7:00" />
                    <Text size={15.5} weight="medium" tone={LOCK.ink}>While you slept</Text>
                    <Counted size={15} tone={LOCK.soft} style={{ lineHeight: 21 }}>Support answered 9 customers. Books chased 4 invoices. Renewals is behind on Globex.</Counted>
                </View>
            </ScrollView>
        </SafeAreaView>
    );
}
