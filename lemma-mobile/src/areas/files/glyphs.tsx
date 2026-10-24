import Svg, { Circle, Path, Rect } from "react-native-svg";
import { color } from "../../ui/tokens";
import type { Glyph } from "./model";

/** The files area's own strokes, on the app's 24-unit grid and in its weight
 *  (`ui/icons.tsx`): what a file is, and the few actions only files have.
 *  Decorative unless the control around them names itself. */

export type FilesIconName = Glyph | "share" | "save" | "trash" | "move" | "comment" | "upload" | "camera" | "spark" | "link" | "minus" | "out";

export function FilesIcon({ name, size = 18, tone = color.ink2, width = 1.7 }: { name: FilesIconName; size?: number; tone?: string; width?: number }) {
    const s = { stroke: tone, strokeWidth: width, fill: "none", strokeLinecap: "round", strokeLinejoin: "round" } as const;
    return (
        <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no">
            {draw(name, s)}
        </Svg>
    );
}

function draw(name: FilesIconName, s: { stroke: string; strokeWidth: number; fill: "none"; strokeLinecap: "round"; strokeLinejoin: "round" }) {
    switch (name) {
        case "folder":
            return <Path {...s} d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17.5z" />;
        case "page":
            return (
                <>
                    <Path {...s} d="M6.5 3.5h7l4 4v13h-11z" />
                    <Path {...s} d="M9 12h6M9 15.5h6M9 8.5h2.5" />
                </>
            );
        case "image":
            return (
                <>
                    <Rect {...s} x={3.5} y={5} width={17} height={14} rx={2} />
                    <Circle {...s} cx={9} cy={10} r={1.6} />
                    <Path {...s} d="m20.5 16-5-5-8 8" />
                </>
            );
        case "video":
            return (
                <>
                    <Rect {...s} x={3.5} y={6} width={12.5} height={12} rx={2} />
                    <Path {...s} d="m16 10.5 4.5-2.5v8l-4.5-2.5" />
                </>
            );
        case "audio":
            return <Path {...s} d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />;
        case "pdf":
            return (
                <>
                    <Path {...s} d="M6.5 3.5h7l4 4v13h-11z" />
                    <Path {...s} d="M13.5 3.5v4h4" />
                    <Path {...s} d="M9 16.5h6" />
                </>
            );
        case "web":
            return (
                <>
                    <Circle {...s} cx={12} cy={12} r={8} />
                    <Path {...s} d="M4 12h16M12 4c2.2 2.3 3.2 5 3.2 8s-1 5.7-3.2 8c-2.2-2.3-3.2-5-3.2-8s1-5.7 3.2-8z" />
                </>
            );
        case "data":
            return (
                <>
                    <Path {...s} d="M6.5 3.5h7l4 4v13h-11z" />
                    <Path {...s} d="M9 11.5h6M9 15h6M12 11.5V18" />
                </>
            );
        case "file":
            return (
                <>
                    <Path {...s} d="M6.5 3.5h7l4 4v13h-11z" />
                    <Path {...s} d="M13.5 3.5v4h4" />
                </>
            );
        case "share":
            return (
                <>
                    <Path {...s} d="M12 15V4M7.5 8.5 12 4l4.5 4.5" />
                    <Path {...s} d="M6 12.5v6a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5v-6" />
                </>
            );
        case "save":
            return (
                <>
                    <Path {...s} d="M12 4v11M7.5 10.5 12 15l4.5-4.5" />
                    <Path {...s} d="M5 19.5h14" />
                </>
            );
        case "upload":
            return (
                <>
                    <Path {...s} d="M12 19V8M7.5 12.5 12 8l4.5 4.5" />
                    <Path {...s} d="M5 4.5h14" />
                </>
            );
        case "trash":
            return (
                <>
                    <Path {...s} d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13" />
                </>
            );
        case "move":
            return (
                <>
                    <Path {...s} d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17.5z" />
                    <Path {...s} d="M9 13.5h6M12.5 11l2.5 2.5-2.5 2.5" />
                </>
            );
        case "comment":
            return <Path {...s} d="M5.5 5h13a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3.5V17h-1a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />;
        case "camera":
            return (
                <>
                    <Path {...s} d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.5l1.5-2h5l1.5 2h2.5A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />
                    <Circle {...s} cx={12} cy={12.5} r={3.2} />
                </>
            );
        case "spark":
            return <Path {...s} d="M12 3.5 13.8 10.2 20.5 12l-6.7 1.8L12 20.5l-1.8-6.7L3.5 12l6.7-1.8z" />;
        case "link":
            return (
                <>
                    <Path {...s} d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
                    <Path {...s} d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
                </>
            );
        case "minus":
            return <Path {...s} d="M6 12h12" />;
        case "out":
            return (
                <>
                    <Path {...s} d="M13.5 4.5h6v6M19.5 4.5 11 13" />
                    <Path {...s} d="M17.5 14v4a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V8A1.5 1.5 0 0 1 6 6.5h4" />
                </>
            );
    }
}
