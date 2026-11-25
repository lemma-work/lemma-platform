import { Platform, View } from "react-native";
import type { BottomTabBarProps } from "expo-router/js-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { TourTarget } from "../areas/more/tour/target";
import { Icon, type IconName } from "./icons";
import { Press } from "./press";
import { Num, Text } from "./text";
import { color } from "./tokens";

/** Home · Needs you · Apps: the web sidebar's places, as tabs (New chat is
 *  Home's ask box; Connectors and Settings are behind your face). Needs you
 *  carries the warm count of what waits on you, as the sidebar does.
 *
 *  Each platform's own furniture: on iPhone a hairline and the current tab
 *  in ink; on Android Material's bar, the current icon in a pill. */

const TABS: Record<string, { label: string; icon: IconName }> = {
    "(home)": { label: "Home", icon: "home" },
    needs: { label: "Needs you", icon: "tray" },
    apps: { label: "Apps", icon: "grid" },
};

const ANDROID = Platform.OS === "android";

export function TabBar({ state, navigation, needs }: BottomTabBarProps & { needs: number }) {
    const insets = useSafeAreaInsets();
    return (
        <View
            accessibilityRole="tablist"
            style={{
                flexDirection: "row",
                paddingTop: ANDROID ? 12 : 6,
                paddingBottom: Math.max(insets.bottom, ANDROID ? 12 : 10),
                backgroundColor: ANDROID ? color.chrome : color.paper,
                ...(ANDROID ? {} : { borderTopWidth: 1, borderTopColor: color.line2 }),
            }}
        >
            {state.routes.map((route, index) => {
                const tab = TABS[route.name];
                if (!tab) return null;
                const focused = state.index === index;
                const tone = focused ? color.ink : ANDROID ? color.ink2 : color.ink3;
                const badge = route.name === "needs" && needs > 0 ? needs : null;
                const tour = route.name === "apps" ? "apps" : route.name === "needs" ? "needs" : null;
                const press = (
                    <Press
                        key={route.key}
                        role="tab"
                        label={tab.label + (badge ? `, ${badge}` : "")}
                        accessibilityState={{ selected: focused }}
                        onPress={() => {
                            const event = navigation.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
                            const go = navigation as unknown as { navigate: (name: string, params?: object) => void };
                            if (!focused && !event.defaultPrevented) go.navigate(route.name);
                            else if (focused) go.navigate(route.name, { screen: "index" });
                        }}
                        style={{ flex: 1, alignItems: "center", gap: ANDROID ? 4 : 2, paddingVertical: 2, minHeight: 48 }}
                    >
                        <View style={ANDROID ? { width: 64, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: focused ? color.faint : "transparent" } : undefined}>
                            <Icon name={tab.icon} size={ANDROID ? 24 : 26} tone={tone} width={1.7} />
                            {badge ? (
                                <View style={{ position: "absolute", top: ANDROID ? -2 : -4, left: ANDROID ? 38 : 16, minWidth: 19, height: 19, paddingHorizontal: 5, borderRadius: 10, backgroundColor: color.warm, alignItems: "center", justifyContent: "center" }}>
                                    <Num size={11} tone={color.onWarm}>{badge > 99 ? "99+" : badge}</Num>
                                </View>
                            ) : null}
                        </View>
                        <Text size={ANDROID ? 12 : 10.5} tone={tone}>{tab.label}</Text>
                    </Press>
                );
                return tour ? <TourTarget key={route.key} id={tour} style={{ flex: 1 }}>{press}</TourTarget> : press;
            })}
        </View>
    );
}
