import Svg, { Circle, Path, Rect } from "react-native-svg";
import { color } from "../ui/tokens";

/** The chat's own glyphs, on the same 24-unit grid and stroke as
 *  `ui/icons.tsx`: what the tool cards, the composer and the thread need and
 *  no other screen does. Decorative unless the control around them names
 *  itself. */

export type GlyphName =
    | "copy" | "globe" | "terminal" | "code" | "image" | "camera" | "stop" | "lock" | "memory"
    | "arrow-down" | "agent" | "plug" | "shield" | "question" | "deny" | "pointer" | "text"
    | "tree" | "folder" | "edit" | "trash" | "move" | "download" | "external" | "eye" | "wait";

export function Glyph({ name, size = 16, tone = color.ink, width = 1.8 }: { name: GlyphName; size?: number; tone?: string; width?: number }) {
    const s = { stroke: tone, strokeWidth: width, fill: "none", strokeLinecap: "round", strokeLinejoin: "round" } as const;
    return (
        <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no">
            {draw(name, s, tone)}
        </Svg>
    );
}

function draw(name: GlyphName, s: { stroke: string; strokeWidth: number; fill: "none"; strokeLinecap: "round"; strokeLinejoin: "round" }, tone: string) {
    switch (name) {
        case "copy":
            return (
                <>
                    <Rect {...s} x={8.5} y={8.5} width={11} height={11} rx={2.2} />
                    <Path {...s} d="M15.5 8.5V6.7a2.2 2.2 0 0 0-2.2-2.2H6.7a2.2 2.2 0 0 0-2.2 2.2v6.6a2.2 2.2 0 0 0 2.2 2.2h1.8" />
                </>
            );
        case "globe":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} d="M4 12h16M12 4c2.4 2.3 3.6 5 3.6 8s-1.2 5.7-3.6 8c-2.4-2.3-3.6-5-3.6-8s1.2-5.7 3.6-8z" />
                </>
            );
        case "terminal":
            return (
                <>
                    <Rect {...s} x={3.5} y={5} width={17} height={14} rx={2.5} />
                    <Path {...s} d="m7.5 10 2.5 2-2.5 2M12.5 14.5h4" />
                </>
            );
        case "code":
            return <Path {...s} d="m8.5 7.5-4.5 4.5 4.5 4.5M15.5 7.5l4.5 4.5-4.5 4.5" />;
        case "image":
            return (
                <>
                    <Rect {...s} x={3.5} y={5} width={17} height={14} rx={2.5} />
                    <Circle {...s} cx={9} cy={10} r={1.6} />
                    <Path {...s} d="m4.5 17 5-4.5 3.5 3 2.5-2 4 3.5" />
                </>
            );
        case "camera":
            return (
                <>
                    <Path {...s} d="M4.5 8.5a2 2 0 0 1 2-2h2l1.5-2h4l1.5 2h2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z" />
                    <Circle {...s} cx={12} cy={12.5} r={3.2} />
                </>
            );
        case "stop":
            return <Rect x={6.5} y={6.5} width={11} height={11} rx={2.2} fill={tone} />;
        case "lock":
            return (
                <>
                    <Rect {...s} x={5.5} y={10.5} width={13} height={9} rx={2} />
                    <Path {...s} d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
                </>
            );
        case "memory":
            return <Path {...s} d="M7 4.5h10a1 1 0 0 1 1 1v14l-6-3.8-6 3.8v-14a1 1 0 0 1 1-1z" />;
        case "arrow-down":
            return <Path {...s} d="M12 5v14M6.5 13.5 12 19l5.5-5.5" />;
        case "agent":
            return (
                <>
                    <Rect {...s} x={5} y={7.5} width={14} height={11} rx={3.5} />
                    <Path {...s} d="M12 7.5V4.5M9.5 12.5v1M14.5 12.5v1" />
                </>
            );
        case "plug":
            return <Path {...s} d="M9 4.5v4M15 4.5v4M7 8.5h10v3a5 5 0 0 1-10 0zM12 16.5v3" />;
        case "shield":
            return <Path {...s} d="M12 3.8 19 6.5v5c0 4.4-3 7.6-7 8.9-4-1.3-7-4.5-7-8.9v-5z" />;
        case "question":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} d="M9.8 9.6a2.3 2.3 0 0 1 4.4.9c0 1.6-2.2 2-2.2 3.4" />
                    <Circle cx={12} cy={16.6} r={1} fill={tone} />
                </>
            );
        case "deny":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} d="m9 9 6 6M15 9l-6 6" />
                </>
            );
        case "pointer":
            return <Path {...s} d="m6 4.5 12 6.2-5.2 1.4 3.2 5.8-2.2 1.2-3.2-5.8L6.6 17z" />;
        case "text":
            return <Path {...s} d="M5 6.5h14M5 11h14M5 15.5h9" />;
        case "tree":
            return <Path {...s} d="M6 5v14M6 9h6M6 15h6M14 7.5h4v3h-4zM14 13.5h4v3h-4z" />;
        case "folder":
            return <Path {...s} d="M4 7.5a2 2 0 0 1 2-2h3.5l2 2.5H18a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" />;
        case "edit":
            return <Path {...s} d="M5 19h3.5L18.5 9 15 5.5 5 15.5zM13 7.5 16.5 11" />;
        case "trash":
            return <Path {...s} d="M5 7h14M9.5 7V5h5v2M7 7l1 12h8l1-12" />;
        case "move":
            return <Path {...s} d="M5 12h13M14 7.5l4.5 4.5-4.5 4.5" />;
        case "download":
            return <Path {...s} d="M12 4.5v10M7.5 10.5 12 15l4.5-4.5M5 19.5h14" />;
        case "external":
            return <Path {...s} d="M13.5 5H19v5.5M19 5l-8 8M17 14v4a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 18V8.5A1.5 1.5 0 0 1 6.5 7h4" />;
        case "eye":
            return (
                <>
                    <Path {...s} d="M3 12c2-4 5-6 9-6s7 2 9 6c-2 4-5 6-9 6s-7-2-9-6z" />
                    <Circle {...s} cx={12} cy={12} r={2.8} />
                </>
            );
        case "wait":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} d="M12 7.5V12l3 2" />
                </>
            );
    }
}
