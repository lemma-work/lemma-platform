import { useEffect, useRef, type ReactNode } from "react";
import { View, type StyleProp, type ViewStyle } from "react-native";
import type { Box, TargetId } from "./model";

/** A control the tour can light, wherever it is drawn: Home's headline and
 *  ask box, its search and profile buttons, the tab bar's tabs. The web finds
 *  its controls by `data-tour`; a phone has no DOM to query, so each one
 *  registers itself here and is measured on the screen when its stop comes. */

/* A stop may light more than one view (Home's headline and the cards
   under it), and lights the box around all of them, as the web does. */
const targets = new Map<TargetId, Set<View>>();

export function TourTarget({ id, style, children }: { id: TargetId; style?: StyleProp<ViewStyle>; children: ReactNode }) {
    const view = useRef<View>(null);
    useEffect(() => {
        const held = view.current;
        if (!held) return;
        const views = targets.get(id) ?? new Set<View>();
        views.add(held);
        targets.set(id, views);
        return () => {
            views.delete(held);
        };
    }, [id]);
    return (
        <View ref={view} collapsable={false} style={style}>
            {children}
        </View>
    );
}

function measureOne(view: View): Promise<Box | null> {
    return new Promise((resolve) => {
        view.measureInWindow((x, y, width, height) => resolve(width > 0 && height > 0 ? { x, y, width, height } : null));
    });
}

/** Where a control is on the screen, or null when it is not drawn. */
export async function measureTarget(id: TargetId): Promise<Box | null> {
    const boxes = (await Promise.all([...(targets.get(id) ?? [])].map(measureOne))).filter((box): box is Box => box !== null);
    if (boxes.length === 0) return null;
    const x = Math.min(...boxes.map((box) => box.x));
    const y = Math.min(...boxes.map((box) => box.y));
    const right = Math.max(...boxes.map((box) => box.x + box.width));
    const bottom = Math.max(...boxes.map((box) => box.y + box.height));
    return { x, y, width: right - x, height: bottom - y };
}
