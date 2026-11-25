import type { ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { Icon } from "./icons";
import { Press } from "./press";
import { Text } from "./text";
import { color, TOUCH } from "./tokens";

/** The pieces every screen's frame is made of: a back link that names
 *  where it goes, a title, a quiet loading line, and an empty state. */

export function BackLink({ label, onPress }: { label?: string; onPress?: () => void }) {
    const router = useRouter();
    return (
        <Press
            role="link"
            label={label ? `Back to ${label}` : "Back"}
            onPress={onPress ?? (() => (router.canGoBack() ? router.back() : router.replace("/")))}
            style={{ flexDirection: "row", alignItems: "center", gap: 2, height: TOUCH, paddingHorizontal: label ? 8 : 11 }}
        >
            <Icon name="back" size={22} width={1.9} />
            {label ? <Text size={16}>{label}</Text> : null}
        </Press>
    );
}

export function IconButton({ name, label, onPress, size = 20 }: { name: Parameters<typeof Icon>[0]["name"]; label: string; onPress: () => void; size?: number }) {
    return (
        <Press label={label} onPress={onPress} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
            <Icon name={name} size={size} />
        </Press>
    );
}

export function PageTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", paddingLeft: 20, paddingRight: 10, paddingBottom: 6 }}>
            <Text accessibilityRole="header" size={28} weight="medium" style={{ flex: 1, letterSpacing: -0.56, lineHeight: 32 }}>{children}</Text>
            {right}
        </View>
    );
}

export function Loading({ label = "Loading" }: { label?: string }) {
    return (
        <View accessibilityLabel={label} style={{ padding: 24, alignItems: "center" }}>
            <ActivityIndicator color={color.ink3} />
        </View>
    );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
    return (
        <View style={{ paddingHorizontal: 24, paddingVertical: 32, gap: 8 }}>
            <Text size={17} weight="medium">{title}</Text>
            {children}
        </View>
    );
}

export function Problem({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
    const said = error instanceof Error ? error.message : "Something went wrong.";
    return (
        <View style={{ margin: 16, padding: 14, borderRadius: 14, backgroundColor: color.amberWash, gap: 8 }}>
            <Text size={14.5} tone={color.amber}>{said}</Text>
            {onRetry ? (
                <Press label="Try again" onPress={onRetry} style={{ alignSelf: "flex-start", minHeight: 36, justifyContent: "center" }}>
                    <Text size={14.5} weight="medium" tone={color.amber}>Try again</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** The fill to a target, as on a Lemma's page: ink to where it is, a tick
 *  where it is aiming. */
/** A number's bar. `tone` fills it in a Lemma's own colour; ink otherwise. */
export function Meter({ fill, mark, label, tone }: { fill: number; mark: number | null; label: string; tone?: string }) {
    return (
        <View accessibilityRole="image" accessibilityLabel={label} style={{ height: 14, justifyContent: "center" }}>
            <View style={{ height: 6, borderRadius: 999, backgroundColor: color.track, overflow: "visible" }}>
                <View style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.round(fill * 1000) / 10}%`, borderRadius: 999, backgroundColor: tone ?? color.ink }} />
                {mark !== null ? <View style={{ position: "absolute", left: `${Math.round(mark * 1000) / 10}%`, top: -4, width: 2, height: 14, borderRadius: 1, backgroundColor: color.ink2 }} /> : null}
            </View>
        </View>
    );
}
