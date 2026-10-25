import Svg, { Path, Rect } from "react-native-svg";
import { color } from "../tokens";

/** The file pieces' own glyphs, on the 24-unit grid and stroke of
 *  `ui/icons.tsx`. Decorative: the control around each names itself. */

export type FileGlyphName = "share" | "play" | "pause" | "camera" | "folder" | "photos" | "sheet" | "page" | "expand";

export function FileGlyph({ name, size = 18, tone = color.ink, width = 1.8 }: { name: FileGlyphName; size?: number; tone?: string; width?: number }) {
    const s = { stroke: tone, strokeWidth: width, fill: "none", strokeLinecap: "round", strokeLinejoin: "round" } as const;
    return (
        <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no">
            {name === "share" ? (
                <>
                    <Path {...s} d="M12 15V3.5M7.5 8 12 3.5 16.5 8" />
                    <Path {...s} d="M8 11H6.5A2 2 0 0 0 4.5 13v6a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2H16" />
                </>
            ) : name === "expand" ? (
                <Path {...s} d="M14 4.5h5.5V10M19.5 4.5 13.5 10.5M10 19.5H4.5V14M4.5 19.5l6-6" />
            ) : name === "play" ? (
                <Path d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.6-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8 0 0 0 8 5.2Z" fill={tone} />
            ) : name === "pause" ? (
                <>
                    <Rect x={6.5} y={5} width={4} height={14} rx={1.2} fill={tone} />
                    <Rect x={13.5} y={5} width={4} height={14} rx={1.2} fill={tone} />
                </>
            ) : name === "camera" ? (
                <>
                    <Path {...s} d="M4 8.5A2 2 0 0 1 6 6.5h1.8l1.5-2h5.4l1.5 2H18a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
                    <Path {...s} d="M12 15.8a3.3 3.3 0 1 0 0-6.6 3.3 3.3 0 0 0 0 6.6Z" />
                </>
            ) : name === "folder" ? (
                <Path {...s} d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.2h7a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z" />
            ) : name === "photos" ? (
                <>
                    <Rect {...s} x={3.5} y={4.5} width={17} height={15} rx={2.5} />
                    <Path {...s} d="m4 16.5 4.5-4.5 3.5 3.5 2.5-2.5 5.5 5.5" />
                    <Path {...s} d="M15.5 9.6a1.4 1.4 0 1 0 0-2.8 1.4 1.4 0 0 0 0 2.8Z" />
                </>
            ) : name === "sheet" ? (
                <>
                    <Rect {...s} x={3.5} y={4.5} width={17} height={15} rx={2} />
                    <Path {...s} d="M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15" />
                </>
            ) : (
                <>
                    <Path {...s} d="M6.5 3.5h7l4 4v12a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1Z" />
                    <Path {...s} d="M13.5 3.5v4h4M8.5 12h7M8.5 15.5h5" />
                </>
            )}
        </Svg>
    );
}
