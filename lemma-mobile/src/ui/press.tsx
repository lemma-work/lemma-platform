import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, View, type PressableProps, type StyleProp, type ViewStyle } from "react-native";
import { Text } from "./text";
import { color, font, TOUCH } from "./tokens";

/** Something a thumb presses. Dims while held; always a button to a screen
 *  reader, with the label it is given. */
export function Press({ children, style, label, role = "button", hitSlop = 6, ...rest }: Omit<PressableProps, "style" | "children"> & {
    children: ReactNode;
    style?: StyleProp<ViewStyle>;
    label?: string;
    role?: "button" | "link" | "tab" | "radio";
}) {
    return (
        <Pressable
            accessibilityRole={role}
            accessibilityLabel={label}
            hitSlop={hitSlop}
            style={({ pressed }) => [style, pressed && { opacity: 0.55 }]}
            {...rest}
        >
            {children}
        </Pressable>
    );
}

/** The canvas's two buttons: ink-filled for the yes, outlined for the rest. */
export function Button({ title, onPress, kind = "primary", busy = false, disabled = false, height = 40, grow = false, size = 14.5 }: {
    title: string;
    onPress: () => void;
    kind?: "primary" | "outline" | "quiet";
    busy?: boolean;
    disabled?: boolean;
    height?: number;
    grow?: boolean;
    size?: number;
}) {
    const primary = kind === "primary";
    return (
        <Press
            onPress={onPress}
            disabled={disabled || busy}
            label={title}
            accessibilityState={{ disabled: disabled || busy, busy }}
            style={{
                minHeight: Math.max(height, TOUCH - 4),
                paddingHorizontal: primary ? 16 : 14,
                borderRadius: 10,
                backgroundColor: primary ? color.ink : "transparent",
                borderWidth: kind === "outline" ? 1 : 0,
                borderColor: color.line2,
                alignItems: "center",
                justifyContent: "center",
                flexGrow: grow ? 1 : 0,
                flexBasis: grow ? 0 : "auto",
                opacity: disabled ? 0.4 : 1,
            }}
        >
            {busy ? (
                <ActivityIndicator size="small" color={primary ? color.onInk : color.ink} />
            ) : (
                <Text size={size} weight={primary ? "medium" : "regular"} tone={primary ? color.onInk : color.ink} numberOfLines={1}>{title}</Text>
            )}
        </Press>
    );
}

/** A small rounded tag: "Your yes" in violet, "Behind" in amber. */
export function Pill({ text, tone }: { text: string; tone: "violet" | "amber" | "grey" }) {
    const palette = tone === "violet" ? { ink: color.violetInk, wash: color.violetWash } : tone === "amber" ? { ink: color.amber, wash: color.amberWash } : { ink: color.ink2, wash: color.wash };
    return (
        <View style={{ backgroundColor: palette.wash, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 }}>
            <Text size={12} tone={palette.ink} style={{ fontFamily: /\d/.test(text) ? font.num : font.regular }}>{text}</Text>
        </View>
    );
}
