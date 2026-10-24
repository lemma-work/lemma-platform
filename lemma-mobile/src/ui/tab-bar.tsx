import { View } from "react-native";
import type { BottomTabBarProps } from "expo-router/js-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { TourTarget } from "../areas/more/tour/target";
import { Icon, type IconName } from "./icons";
import { Press } from "./press";
import { Num, Text } from "./text";
import { color } from "./tokens";

/** Home · Lemmas · Apps, drawn as the canvas draws it: a hairline above,
 *  the current tab in ink, the rest in grey. Home carries the count of what
 *  needs you while you are elsewhere; Lemmas a violet dot when one of its
 *  rows waits on you. */

const TABS: Record<string, { label: string; icon: IconName }> = {
    "(home)": { label: "Home", icon: "home" },
    lemmas: { label: "Lemmas", icon: "lemmas" },
    apps: { label: "Apps", icon: "apps" },
};

export function TabBar({ state, navigation, needs, lemmasWaiting }: BottomTabBarProps & { needs: number; lemmasWaiting: boolean }) {
    const insets = useSafeAreaInsets();
    return (
        <View
            accessibilityRole="tablist"
            style={{ flexDirection: "row", borderTopWidth: 1, borderTopColor: color.line, paddingTop: 6, paddingBottom: Math.max(insets.bottom, 10), backgroundColor: color.paper }}
        >
            {state.routes.map((route, index) => {
                const tab = TABS[route.name];
                if (!tab) return null;
                const focused = state.index === index;
                const tone = focused ? color.ink : color.ink2;
                const badge = route.name === "(home)" && !focused && needs > 0 ? needs : null;
                const dot = route.name === "lemmas" && !focused && lemmasWaiting;
                const tour = route.name === "lemmas" ? "lemmas" : route.name === "apps" ? "apps" : null;
                const press = (
                    <Press
                        key={route.key}
                        role="tab"
                        label={tab.label + (badge ? `, ${badge} need you` : dot ? ", something needs you" : "")}
                        accessibilityState={{ selected: focused }}
                        onPress={() => {
                            const event = navigation.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
                            const go = navigation as unknown as { navigate: (name: string, params?: object) => void };
                            if (!focused && !event.defaultPrevented) go.navigate(route.name);
                            else if (focused) go.navigate(route.name, { screen: "index" });
                        }}
                        style={{ flex: 1, alignItems: "center", gap: 3, paddingVertical: 4, minHeight: 48 }}
                    >
                        <View>
                            <Icon name={tab.icon} size={23} tone={tone} />
                            {badge ? (
                                <View style={{ position: "absolute", top: -5, left: 14, minWidth: 18, height: 18, paddingHorizontal: 5, borderRadius: 999, backgroundColor: color.violet, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: color.paper }}>
                                    <Num size={11} tone={color.onViolet}>{badge > 99 ? "99+" : badge}</Num>
                                </View>
                            ) : null}
                            {dot ? <View style={{ position: "absolute", top: -1, left: 18, width: 10, height: 10, borderRadius: 5, backgroundColor: color.violet, borderWidth: 2, borderColor: color.paper }} /> : null}
                        </View>
                        <Text size={11.5} tone={tone}>{tab.label}</Text>
                    </Press>
                );
                /* The tour lights these two (area G, `TourTarget`). */
                return tour ? <TourTarget key={route.key} id={tour} style={{ flex: 1 }}>{press}</TourTarget> : press;
            })}
        </View>
    );
}
