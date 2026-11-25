import { ScrollView, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useApps, useLemma } from "../../../../../data/hooks";
import { BackLink, Loading } from "../../../../../ui/chrome";
import { LemmaFace } from "../../../../../ui/face";
import { SECTIONS, SectionBody, isSection } from "../../../../../ui/lemmas/sections";
import { Text } from "../../../../../ui/text";
import { color } from "../../../../../ui/tokens";

/** One of a Lemma's sections on a screen of its own: its chats, apps,
 *  tables, files, runs, people, plan or settings. */
export default function LemmaSection() {
    const params = useLocalSearchParams<{ podId: string; name: string }>();
    const podId = String(params.podId);
    const name = String(params.name);
    const lemma = useLemma(podId);
    const { apps } = useApps();
    const label = SECTIONS.find((one) => one.name === name)?.label ?? "";
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 4, paddingHorizontal: 6 }}>
                <BackLink label={lemma?.name ?? "Back"} />
            </View>
            {!lemma ? <Loading /> : !isSection(name) ? null : (
                <ScrollView contentContainerStyle={{ paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 20, paddingTop: 4, paddingBottom: 10 }}>
                        <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={28} />
                        <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{label}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 16 }}>
                        <SectionBody name={name} podId={podId} lemmaName={lemma.name} apps={apps.filter((app) => app.podId === podId)} />
                    </View>
                </ScrollView>
            )}
        </SafeAreaView>
    );
}
