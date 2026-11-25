import { View } from "react-native";
import Svg, { Path } from "react-native-svg";
import { DRAWINGS, drawingOf } from "@web/outcomes/app-drawings";
import { tintInkOf, tintOf } from "./face";

/** An app's mark, as the web draws it (`outcomes/app-mark.tsx`): a line
 *  drawing read from its name, in a circle of its own tint. Apps carry no
 *  icon of their own, so this is how two apps tell apart at a glance. */
export function AppMark({ name, label = "", size = 22, shadow = false }: { name: string; label?: string; size?: number; shadow?: boolean }) {
    const seed = "app:" + name;
    const ink = tintInkOf(seed);
    const glyph = Math.max(10, Math.round(size * 0.5));
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{
                width: size,
                height: size,
                borderRadius: size / 2,
                backgroundColor: tintOf(seed),
                alignItems: "center",
                justifyContent: "center",
                ...(shadow ? { shadowColor: "#000", shadowOpacity: 0.12, shadowRadius: 8, shadowOffset: { width: 0, height: 4 }, elevation: 3 } : {}),
            }}
        >
            <Svg width={glyph} height={glyph} viewBox="0 0 24 24">
                <Path d={DRAWINGS[drawingOf(name, label)]} stroke={ink} strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" fill="none" />
            </Svg>
        </View>
    );
}
