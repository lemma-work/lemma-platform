import Svg, { Circle, Path, Rect } from "react-native-svg";
import { color } from "./tokens";

/** The canvas's stroke icons, one 24-unit grid. Decorative unless the
 *  control around them names itself. */

export type IconName =
    | "home" | "lemmas" | "apps" | "search" | "plus" | "down" | "back" | "forward" | "mic" | "send"
    | "clip" | "more" | "pencil" | "whatsapp" | "mail" | "automation" | "table" | "clock" | "app"
    | "people" | "telegram" | "photo" | "check" | "close" | "slack" | "bell" | "file" | "refresh" | "chat" | "list" | "tray" | "grid" | "bookmark" | "book" | "agent" | "plug" | "spark" | "link";

export function Icon({ name, size = 20, tone = color.ink, width = 1.8 }: { name: IconName; size?: number; tone?: string; width?: number }) {
    const stroke = { stroke: tone, strokeWidth: width, fill: "none" } as const;
    return (
        <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no">
            {draw(name, stroke, tone)}
        </Svg>
    );
}

function draw(name: IconName, s: { stroke: string; strokeWidth: number; fill: "none" }, tone: string) {
    switch (name) {
        case "home":
            return <Path {...s} strokeLinejoin="round" d="M4.5 10.5 12 4.5l7.5 6V19a1 1 0 0 1-1 1h-4v-5.5h-5V20h-4a1 1 0 0 1-1-1z" />;
        case "lemmas":
            return (
                <>
                    <Rect {...s} x={4.5} y={4.5} width={6} height={6} rx={1.8} />
                    <Rect {...s} x={13.5} y={4.5} width={6} height={6} rx={1.8} />
                    <Rect {...s} x={4.5} y={13.5} width={6} height={6} rx={1.8} />
                    <Rect {...s} x={13.5} y={13.5} width={6} height={6} rx={1.8} />
                </>
            );
        case "apps":
        case "app":
            return (
                <>
                    <Rect {...s} x={3.5} y={5} width={17} height={14} rx={2.5} />
                    <Path {...s} d="M3.5 9h17" />
                </>
            );
        case "search":
            return (
                <>
                    <Circle {...s} cx={11} cy={11} r={6.5} />
                    <Path {...s} strokeLinecap="round" d="m16 16 4 4" />
                </>
            );
        case "tray":
            return (
                <>
                    <Path {...s} strokeLinejoin="round" d="M4 13.5 6.5 5h11l2.5 8.5V19H4z" />
                    <Path {...s} strokeLinejoin="round" d="M4 13.5h5l1.2 2h3.6l1.2-2h5" />
                </>
            );
        case "bookmark":
            return <Path {...s} strokeLinejoin="round" d="M7 3.5h10v17l-5-3.5-5 3.5z" />;
        case "book":
            return (
                <>
                    <Path {...s} strokeLinejoin="round" d="M5 4.5h10.5a3 3 0 0 1 3 3v12H8a3 3 0 0 1-3-3z" />
                    <Path {...s} strokeLinejoin="round" d="M5 16.5a3 3 0 0 1 3-3h10.5" />
                </>
            );
        case "agent":
            return (
                <>
                    <Rect {...s} x={5} y={8} width={14} height={11} rx={3} />
                    <Path {...s} strokeLinecap="round" d="M12 4.5V8" />
                    <Circle cx={9.5} cy={13.5} r={1.1} fill={tone} />
                    <Circle cx={14.5} cy={13.5} r={1.1} fill={tone} />
                </>
            );
        case "plug":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M9 3.5V8M15 3.5V8M7 8h10v3.5a5 5 0 0 1-10 0zM12 16.5v4" />;
        case "spark":
            return <Path {...s} strokeLinejoin="round" d="M12 3.5l1.9 5.6 5.6 1.9-5.6 1.9L12 18.5l-1.9-5.6L4.5 11l5.6-1.9z" />;
        case "link":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />;
        case "grid":
            return (
                <>
                    <Rect {...s} x={4} y={4} width={6.5} height={6.5} rx={1.8} />
                    <Rect {...s} x={13.5} y={4} width={6.5} height={6.5} rx={1.8} />
                    <Rect {...s} x={4} y={13.5} width={6.5} height={6.5} rx={1.8} />
                    <Rect {...s} x={13.5} y={13.5} width={6.5} height={6.5} rx={1.8} />
                </>
            );
        case "list":
            return <Path {...s} strokeLinecap="round" d="M5 7h14M5 12h14M5 17h14" />;
        case "chat":
            return <Path {...s} strokeLinejoin="round" d="M5.5 5h13a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3.5V17h-1a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />;
        case "plus":
            return <Path {...s} strokeLinecap="round" d="M12 5v14M5 12h14" />;
        case "down":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="m7 10 5 5 5-5" />;
        case "back":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="m14.5 6-6 6 6 6" />;
        case "forward":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="m9.5 6 6 6-6 6" />;
        case "mic":
            return (
                <>
                    <Rect {...s} x={9} y={3.5} width={6} height={11} rx={3} />
                    <Path {...s} strokeLinecap="round" d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V20.5" />
                </>
            );
        case "send":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M12 19V5M6 11l6-6 6 6" />;
        case "clip":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="m20 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1L13 4.3a3.3 3.3 0 0 1 4.7 4.7l-8.3 8.3a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6" />;
        case "more":
            return (
                <>
                    <Circle cx={6} cy={12} r={1.6} fill={tone} />
                    <Circle cx={12} cy={12} r={1.6} fill={tone} />
                    <Circle cx={18} cy={12} r={1.6} fill={tone} />
                </>
            );
        case "pencil":
            return (
                <>
                    <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M12 20h8" />
                    <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                </>
            );
        case "whatsapp":
            return (
                <>
                    <Path {...s} strokeLinejoin="round" d="M4.5 19.5 5.6 16A8 8 0 1 1 8.4 18.6z" />
                    <Path {...s} strokeLinejoin="round" d="M9.5 9.5c.3 1.9 2.1 3.8 4 4.2l1-1 1.6.8" />
                </>
            );
        case "telegram":
            return (
                <>
                    <Path {...s} strokeLinejoin="round" d="M20.5 4 3.5 11l6 2.5L12 20z" />
                    <Path {...s} strokeLinejoin="round" d="m9.5 13.5 4-4" />
                </>
            );
        case "slack":
            return <Path {...s} strokeLinecap="round" d="M9.5 4v16M14.5 4v16M4 9.5h16M4 14.5h16" />;
        case "mail":
            return (
                <>
                    <Rect {...s} x={3.5} y={5.5} width={17} height={13} rx={2} />
                    <Path {...s} strokeLinejoin="round" d="m4 7 8 6 8-6" />
                </>
            );
        case "automation":
            return (
                <>
                    <Circle {...s} cx={6} cy={6} r={2.2} />
                    <Circle {...s} cx={18} cy={18} r={2.2} />
                    <Path {...s} strokeLinecap="round" d="M8.2 6H14a3 3 0 0 1 3 3v6.8" />
                </>
            );
        case "table":
            return (
                <>
                    <Rect {...s} x={4} y={5} width={16} height={14} rx={2} />
                    <Path {...s} d="M4 10h16M10 10v9" />
                </>
            );
        case "clock":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} strokeLinecap="round" d="M12 8v4l3 2" />
                </>
            );
        case "people":
            return (
                <>
                    <Circle {...s} cx={9} cy={9} r={3} />
                    <Path {...s} strokeLinecap="round" d="M3.5 19c.8-3 2.9-4.5 5.5-4.5s4.7 1.5 5.5 4.5" />
                    <Path {...s} strokeLinecap="round" d="M15.5 6.2a3 3 0 0 1 0 5.6M17.5 14.8c1.5.6 2.5 2 3 4.2" />
                </>
            );
        case "photo":
            return (
                <>
                    <Rect {...s} x={3.5} y={5} width={17} height={14} rx={2} />
                    <Circle {...s} cx={9} cy={10} r={1.6} />
                    <Path {...s} d="m20.5 16-5-5-8 8" />
                </>
            );
        case "check":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="m5 12.5 4.5 4.5L19 7.5" />;
        case "close":
            return <Path {...s} strokeLinecap="round" d="M6 6l12 12M18 6 6 18" />;
        case "bell":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5h4" />;
        case "file":
            return (
                <>
                    <Path {...s} strokeLinejoin="round" d="M6.5 3.5h7l4 4v13h-11z" />
                    <Path {...s} strokeLinejoin="round" d="M13.5 3.5v4h4" />
                </>
            );
        case "refresh":
            return <Path {...s} strokeLinecap="round" strokeLinejoin="round" d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4" />;
    }
}

/** The Lemma mark: three rising bars, never dots. */
export function LemmaMark({ size = 20, tone = color.violet }: { size?: number; tone?: string }) {
    return (
        <Svg width={size} height={size} viewBox="0 0 32 32" accessibilityElementsHidden importantForAccessibility="no">
            <Rect x={4} y={19} width={5} height={10} rx={1.5} fill={tone} />
            <Rect x={13} y={12} width={5} height={17} rx={1.5} fill={tone} />
            <Rect x={22} y={3} width={5} height={26} rx={1.5} fill={tone} />
        </Svg>
    );
}
