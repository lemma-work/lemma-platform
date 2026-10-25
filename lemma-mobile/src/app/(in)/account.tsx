import { KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useMe, useOrg } from "../../data/hooks";
import { routes } from "../../data/routes";
import { useSession } from "../../session/session";
import { PersonFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { ServerPicker } from "../../ui/server-picker";
import { adminRoutes } from "../../areas/admin/routes";
import { useDeployment } from "../../areas/admin/access/hooks";
import { offeredSettings } from "../../areas/admin/sections";
import { moreRoutes } from "../../areas/more/routes";
import { HelpGroup } from "../../areas/more/tour/help";
import { useTheme, type Appearance } from "../../ui/theme";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";

/** Who is signed in, which organization the app is in, and the way out. */
export default function Account() {
    const router = useRouter();
    const { mode, server, signOut, setServer } = useSession();
    const me = useMe();
    const { org, orgs, pick } = useOrg();
    const { appearance, setAppearance } = useTheme();
    /* No billing on a local installation, as on the web. */
    const settings = offeredSettings(useDeployment());
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingHorizontal: 8 }}>
                <Press label="Done" onPress={() => router.back()} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                    <Text size={16}>Done</Text>
                </Press>
            </View>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 24, gap: 22 }} keyboardShouldPersistTaps="handled">
                <View style={{ flexDirection: "row", alignItems: "center", gap: 14, paddingTop: 6 }}>
                    <PersonFace name={me.data?.name ?? "You"} size={48} dark />
                    <View style={{ flex: 1 }}>
                        <Text size={20} weight="medium">{me.data?.name ?? " "}</Text>
                        <Text size={14} tone={color.ink2}>{mode === "sample" ? "Sample data" : `${me.data?.email ?? ""} · ${server.label}`}</Text>
                    </View>
                </View>

                {orgs.length > 0 ? (
                    <View style={{ gap: 4 }}>
                        <Text size={12.5} tone={color.ink3}>Organization</Text>
                        {orgs.map((one) => (
                            <Press key={one.id} label={one.name} accessibilityState={{ selected: one.id === org?.id }} onPress={() => { pick(one.id); router.back(); }} style={{ flexDirection: "row", alignItems: "center", minHeight: 48, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                <Text size={16} style={{ flex: 1 }}>{one.name}</Text>
                                {one.id === org?.id ? <Icon name="check" size={18} width={2} /> : null}
                            </Press>
                        ))}
                        {/* Another organization, and the invitations and
                            organizations waiting to be joined (area G). */}
                        <Press role="link" label="New organization" onPress={() => router.push(moreRoutes.newOrg as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 48, borderBottomWidth: 1, borderBottomColor: color.line }}>
                            <Icon name="plus" size={18} tone={color.ink2} />
                            <Text size={16} style={{ flex: 1 }}>New organization</Text>
                        </Press>
                        <Press role="link" label="Invitations and organizations to join" onPress={() => router.push(moreRoutes.welcome as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 48, borderBottomWidth: 1, borderBottomColor: color.line }}>
                            <Icon name="mail" size={18} tone={color.ink2} />
                            <Text size={16} style={{ flex: 1 }}>Invitations and organizations to join</Text>
                            <Icon name="forward" size={16} tone={color.ink3} />
                        </Press>
                    </View>
                ) : null}

                <View style={{ gap: 4 }}>
                    <Text size={12.5} tone={color.ink3}>Where the work runs</Text>
                    <Press role="link" label="Your computer" onPress={() => router.push(moreRoutes.computer() as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 56, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <View style={{ flex: 1 }}>
                            <Text size={16}>Your computer</Text>
                            <Text size={13} tone={color.ink3}>Where your Lemmas work: its screen, files and signed-in sites</Text>
                        </View>
                        <Icon name="forward" size={16} tone={color.ink3} />
                    </Press>
                </View>

                <View style={{ gap: 4 }}>
                    <Text size={12.5} tone={color.ink3}>On this phone</Text>
                    <Press role="link" label="Notifications, a preview" onPress={() => router.push(routes.notices as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 48, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <Icon name="bell" size={18} tone={color.ink2} />
                        <View style={{ flex: 1 }}>
                            <Text size={16}>Notifications</Text>
                            <Text size={13} tone={color.ink3}>A preview: nothing sends them yet</Text>
                        </View>
                        <Icon name="forward" size={16} tone={color.ink3} />
                    </Press>
                </View>

                <View style={{ gap: 8 }}>
                    <Text size={12.5} tone={color.ink3}>Appearance</Text>
                    <View accessibilityRole="radiogroup" style={{ flexDirection: "row", gap: 8 }}>
                        {(["system", "light", "dark"] as const satisfies readonly Appearance[]).map((one) => {
                            const on = one === appearance;
                            const label = one === "system" ? "As the phone" : one === "light" ? "Light" : "Dark";
                            return (
                                <Press key={one} role="radio" label={label} accessibilityState={{ selected: on }} onPress={() => setAppearance(one)} style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
                                    <Text size={14} tone={on ? color.onInk : color.ink}>{label}</Text>
                                </Press>
                            );
                        })}
                    </View>
                </View>

                {(["you", "organization"] as const).map((group) => (
                    <View key={group} style={{ gap: 4 }}>
                        <Text size={12.5} tone={color.ink3}>{group === "you" ? "You" : org?.name ?? "Organization"}</Text>
                        {settings.filter((one) => one.group === group).map((one) => (
                            <Press key={one.id} role="link" label={one.label} onPress={() => router.push(adminRoutes.section(one.id) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 56, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                <View style={{ flex: 1 }}>
                                    <Text size={16}>{one.label}</Text>
                                    <Text size={13} tone={color.ink3}>{one.hint}</Text>
                                </View>
                                <Icon name="forward" size={16} tone={color.ink3} />
                            </Press>
                        ))}
                    </View>
                ))}

                <HelpGroup />

                <View style={{ gap: 4 }}>
                    <Text size={12.5} tone={color.ink3}>Server</Text>
                    <ServerPicker
                        current={server}
                        onPick={(next) => {
                            if (next.id === server.id && mode !== "sample") return;
                            /* Each server keeps its own session: one already signed
                               in opens at once, any other at sign-in. */
                            void setServer(next);
                        }}
                    />
                </View>

                <Button title={mode === "sample" ? "Leave sample data" : "Sign out"} kind="outline" height={48} onPress={() => void signOut()} />
            </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
