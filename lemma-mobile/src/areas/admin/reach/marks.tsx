import Svg, { Circle, Path, Rect } from "react-native-svg";
import { Icon } from "../../../ui/icons";
import { color } from "../../../ui/tokens";
import { placeGlyph } from "./places";

/** A platform's glyph, in the app's stroke style: the app's own icons where
 *  it has them, and Teams and Discord drawn here on the same 24-unit grid. */
export function PlaceMark({ platform, size = 18, tone }: { platform: string; size?: number; tone?: string }) {
    const ink = tone ?? color.ink2;
    const glyph = placeGlyph(platform);
    if (glyph === "whatsapp" || glyph === "telegram" || glyph === "mail" || glyph === "slack") return <Icon name={glyph} size={size} tone={ink} />;
    if (glyph === "other") return <Icon name="chat" size={size} tone={ink} />;
    const s = { stroke: ink, strokeWidth: 1.8, fill: "none" } as const;
    return (
        <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no">
            {glyph === "teams" ? (
                <>
                    <Rect {...s} x={3.5} y={6} width={12} height={12} rx={2.2} />
                    <Path {...s} strokeLinecap="round" d="M6.8 9.5h5.4M9.5 9.5v5.5" />
                    <Circle {...s} cx={18} cy={7.5} r={2} />
                    <Path {...s} strokeLinejoin="round" d="M15.5 11h4a1 1 0 0 1 1 1v3.2a2.8 2.8 0 0 1-5 1.7" />
                </>
            ) : (
                <>
                    <Path {...s} strokeLinejoin="round" d="M7 7.2c3.2-1.4 6.8-1.4 10 0l1.9 8.1c-1.6 1.4-3.3 2.2-5 2.5l-.9-1.5h-2l-.9 1.5c-1.7-.3-3.4-1.1-5-2.5z" />
                    <Circle cx={9.6} cy={12.4} r={1.1} fill={ink} />
                    <Circle cx={14.4} cy={12.4} r={1.1} fill={ink} />
                </>
            )}
        </Svg>
    );
}
