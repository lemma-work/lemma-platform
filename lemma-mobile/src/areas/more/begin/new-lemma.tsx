import { useMemo, useState, type ReactNode } from "react";
import { KeyboardAvoidingView, ScrollView, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { CHARACTERS } from "@web/shell/cast";
import { READY } from "@web/outcomes/ready-starts";
import { useLemmas } from "../../../data/hooks";
import { Loading } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { haptics } from "../../../ui/haptics";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, font } from "../../../ui/tokens";
import { Wash } from "../../../ui/wash";
import { moreRoutes } from "../routes";
import { useStartLemma } from "./hooks";
import { castIcon, standInWords, startedLanding } from "./model";

/** "New Lemma", as the web makes one (`outcomes/start-lemma.tsx`): its face
 *  first, one none of the organization's Lemmas wears yet; then a name and
 *  what it is for, or one of the ready starts filled in with a tap; who can
 *  find it; and "Make it", which opens its page. People and apps are added
 *  from there, where the web's second and third steps add them. */
export function NewLemmaScreen({ top }: {
    /** Where the install part's "You were installing …" line goes. */
    top?: ReactNode;
}) {
    const router = useRouter();
    const { org, lemmas, pending } = useLemmas();
    const starting = useStartLemma(org?.id ?? null);
    const [name, setName] = useState("");
    const [purpose, setPurpose] = useState("");
    const [reach, setReach] = useState<"invited" | "org">("invited");
    const [standIn, setStandIn] = useState<string | null>(null);
    /* A face nobody here wears yet, while there is one left (`freshFace`). */
    const face = useMemo(() => {
        const worn = new Set(lemmas.map((one) => one.iconUrl ?? ""));
        return CHARACTERS.find((one) => !worn.has(castIcon(one) ?? "")) ?? CHARACTERS[lemmas.length % CHARACTERS.length];
    }, [lemmas]);
    const shown = name.trim() || "New Lemma";

    const make = () => {
        if (!org || !name.trim() || starting.isPending) return;
        starting.mutate({ orgId: org.id, name, purpose, face, reach }, {
            onSuccess: (started) => {
                haptics.done();
                if (started.standsIn) {
                    setStandIn(standInWords(started.name, started.standsIn.name));
                    setTimeout(() => router.replace(startedLanding(started) as never), 1800);
                    return;
                }
                router.replace(startedLanding(started) as never);
            },
        });
    };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <Wash height={360} />
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", paddingHorizontal: 8, paddingTop: 4 }}>
                    <Press label="Cancel" onPress={() => router.back()} style={{ height: 44, paddingHorizontal: 10, justifyContent: "center" }}>
                        <Text size={16} tone={color.ink2}>Cancel</Text>
                    </Press>
                </View>
                <ScrollView contentContainerStyle={{ paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                    <View style={{ alignItems: "center", gap: 8, paddingTop: 4, paddingBottom: 20 }}>
                        <View style={{ shadowColor: color.violet, shadowOpacity: 0.18, shadowRadius: 20, shadowOffset: { width: 0, height: 12 }, elevation: 5 }}>
                            <LemmaFace id={"new:" + face} icon={castIcon(face)} size={96} />
                        </View>
                        <Text size={20} weight="medium" numberOfLines={1}>{shown}</Text>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 7 }}>
                            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: starting.isPending ? color.violet : color.ok }} />
                            <Text size={13.5} tone={color.ink3}>{starting.isPending ? "Making…" : "Listening"}</Text>
                        </View>
                    </View>

                    <View style={{ paddingHorizontal: 20, gap: 14 }}>
                        {top}
                        {pending ? <Loading label="Reading your Lemmas" /> : !org ? (
                            <View style={{ gap: 12 }}>
                                <Text size={16} tone={color.ink2}>You’re not in an organization yet, and a Lemma starts in one.</Text>
                                <View style={{ flexDirection: "row" }}>
                                    <Button title="Find or start one" onPress={() => router.push(moreRoutes.welcome as never)} />
                                </View>
                            </View>
                        ) : (
                            <>
                                <Text accessibilityRole="header" size={25} weight="medium" style={{ letterSpacing: -0.5, lineHeight: 30 }}>What’s this Lemma for?</Text>
                                <TextInput
                                    value={name}
                                    onChangeText={setName}
                                    placeholder="Give it a name"
                                    placeholderTextColor={color.ink3}
                                    accessibilityLabel="Its name"
                                    maxLength={80}
                                    autoFocus
                                    returnKeyType="next"
                                    style={{ height: 54, paddingHorizontal: 16, borderRadius: 14, borderWidth: 1, borderColor: name ? color.ink : color.line2, backgroundColor: color.paper, fontFamily: font.regular, fontSize: 19, color: color.ink }}
                                />
                                <TextInput
                                    value={purpose}
                                    onChangeText={setPurpose}
                                    placeholder="What it does, in a sentence"
                                    placeholderTextColor={color.ink3}
                                    accessibilityLabel="What it does"
                                    maxLength={240}
                                    multiline
                                    style={{ minHeight: 52, paddingHorizontal: 16, paddingVertical: 14, borderRadius: 14, borderWidth: 1, borderColor: color.line2, backgroundColor: color.paper, fontFamily: font.regular, fontSize: 16, color: color.ink }}
                                />

                                <Text size={13.5} tone={color.ink3} style={{ marginTop: 4 }}>Or start from one that’s ready</Text>
                                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                    {READY.map((one) => {
                                        const on = name === one.name && purpose === one.purpose;
                                        return (
                                            <Press
                                                key={one.name}
                                                label={`${one.name}: ${one.purpose}`}
                                                accessibilityState={{ selected: on }}
                                                onPress={() => { setName(one.name); setPurpose(one.purpose); }}
                                                style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, borderWidth: 1, borderColor: on ? color.info : color.line2, backgroundColor: on ? color.pick : color.paper, justifyContent: "center" }}
                                            >
                                                <Text size={14.5} tone={on ? color.info : color.ink}>{one.name}</Text>
                                            </Press>
                                        );
                                    })}
                                </View>

                                <Text size={13.5} tone={color.ink3} style={{ marginTop: 6 }}>Who can find it</Text>
                                <View accessibilityRole="radiogroup" style={{ gap: 8 }}>
                                    {([["invited", "Only people I add", "Only the people you add will see it."], ["org", `Everyone at ${org.name}`, `Anyone at ${org.name} can find it and join.`]] as const).map(([key, label, line]) => {
                                        const on = reach === key;
                                        return (
                                            <Press
                                                key={key}
                                                role="radio"
                                                label={label}
                                                accessibilityState={{ checked: on }}
                                                onPress={() => setReach(key)}
                                                style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 58, paddingHorizontal: 14, borderRadius: 14, borderWidth: 1, borderColor: on ? color.info : color.line2, backgroundColor: on ? color.pick : color.paper }}
                                            >
                                                <View style={{ flex: 1 }}>
                                                    <Text size={15.5} tone={on ? color.info : color.ink}>{label}</Text>
                                                    <Text size={13} tone={color.ink3}>{line}</Text>
                                                </View>
                                                {on ? <Icon name="check" size={18} tone={color.info} width={2.2} /> : null}
                                            </Press>
                                        );
                                    })}
                                </View>

                                {starting.error ? <Text size={14} tone={color.amber} accessibilityRole="alert">{starting.error.message}</Text> : null}
                                {standIn ? <Text size={14} tone={color.ink2} accessibilityLiveRegion="polite">{standIn}</Text> : null}
                                <View style={{ marginTop: 6 }}>
                                    <Button title={starting.isPending ? "Making…" : "Make it"} onPress={make} busy={starting.isPending} disabled={!name.trim() || starting.isPending} height={52} size={16} grow />
                                </View>
                            </>
                        )}
                    </View>
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
