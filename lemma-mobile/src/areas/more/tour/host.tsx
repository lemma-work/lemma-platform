import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal, View, useWindowDimensions } from "react-native";
import { useIsFocused } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Path } from "react-native-svg";
import { useSession } from "../../../session/session";
import { IconButton } from "../../../ui/chrome";
import { LemmaMark } from "../../../ui/icons";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { installArrival } from "../install/arrival";
import { onTourAsked } from "./bus";
import { useTourAccount } from "./hooks";
import { cardPlace, lit, offersTour, scrimPath, tourStops, type Box, type Stop } from "./model";
import { readTourSeen, writeTourSeen } from "./seen";
import { measureTarget } from "./target";

/** The tour, over Home (the web's `AppTour`, for a phone).
 *
 *  Mounted once on Home. It offers itself once, in the account's first
 *  week, when Home is ready and nothing else is up (`ready`); it runs again
 *  whenever somebody asks under Help on the account screen. Each stop lights
 *  the real control: the screen dims everywhere but there, the card sits
 *  along the edge the control is not near, and nothing underneath can be
 *  pressed while it is up. Back on Android ends it, as Escape does. */
export function TourHost({ org, ready }: { org: string | null; ready: boolean }) {
    const { mode } = useSession();
    const account = useTourAccount();
    const [touring, setTouring] = useState(false);
    const [offered, setOffered] = useState(false);
    /* Home's controls stay registered under whatever is pushed over it, so
       the tour runs only while Home is the screen in front: asked for from
       elsewhere, it waits for Home. */
    const focused = useIsFocused();

    /* Offered once, and not in sample data, where there is no account and
       anybody judging a screen would meet it on every launch. */
    useEffect(() => {
        /* The visit an install opens is for what was installed: the tour
           is offered on the next one, as on the web. */
        if (mode !== "live" || offered || !ready || !focused || !account.data?.id || installArrival.held()) return;
        const { id, createdAt } = account.data;
        let cancelled = false;
        const timer = setTimeout(() => {
            void readTourSeen(id).then((seen) => {
                if (cancelled || !offersTour(createdAt, { seen })) return;
                setOffered(true);
                setTouring(true);
            });
        }, 1200);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [mode, offered, ready, focused, account.data]);

    useEffect(() => onTourAsked(() => setTouring(true)), []);

    const end = useCallback(() => {
        setTouring(false);
        setOffered(true);
        const id = account.data?.id;
        if (id && mode === "live") void writeTourSeen(id);
    }, [account.data?.id, mode]);

    const stops = useMemo(() => tourStops(org), [org]);
    if (!touring || !focused) return null;
    return <Tour stops={stops} onClose={end} />;
}

function Tour({ stops, onClose }: { stops: Stop[]; onClose: () => void }) {
    const view = useWindowDimensions();
    const insets = useSafeAreaInsets();
    const [at, setAt] = useState(0);
    const [box, setBox] = useState<Box | null>(null);
    const stop = stops[Math.min(at, stops.length - 1)];
    const last = at === stops.length - 1;

    /* Measured when the stop comes, and once more after the screen has
       settled: a keyboard going away or a list landing moves things. */
    useEffect(() => {
        setBox(null);
        if (!stop.target) return;
        const target = stop.target;
        let alive = true;
        const read = () => void measureTarget(target).then((found) => { if (alive) setBox(found); });
        read();
        const again = setTimeout(read, 300);
        return () => {
            alive = false;
            clearTimeout(again);
        };
    }, [stop, view.width, view.height]);

    const hole = box ? lit(box, view) : null;
    const place = cardPlace(stop.target ? hole : null, view, insets);
    const next = () => (last ? onClose() : setAt((was) => was + 1));
    const back = () => setAt((was) => Math.max(0, was - 1));
    const count = stops.length - 1;

    return (
        <Modal visible transparent animationType="fade" statusBarTranslucent onRequestClose={onClose}>
            <View
                accessibilityViewIsModal
                style={{
                    flex: 1,
                    paddingHorizontal: 12,
                    justifyContent: place.edge === "center" ? "center" : place.edge === "top" ? "flex-start" : "flex-end",
                    paddingTop: place.top ?? insets.top + 12,
                    paddingBottom: place.bottom ?? insets.bottom + 12,
                }}
            >
                <Svg width={view.width} height={view.height} style={{ position: "absolute", left: 0, top: 0 }} pointerEvents="none">
                    <Path d={scrimPath(view, stop.target ? hole : null)} fill={color.scrim} fillRule="evenodd" />
                </Svg>
                <View
                    accessibilityRole="alert"
                    accessibilityLabel={stop.title + ". " + stop.line}
                    style={{
                        backgroundColor: color.paper,
                        borderRadius: 18,
                        paddingTop: stop.id === "welcome" ? 20 : 8,
                        paddingBottom: 16,
                        paddingHorizontal: 18,
                        gap: 10,
                    }}
                >
                    {stop.id === "welcome" ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                            <LemmaMark size={22} />
                            <Text size={13} tone={color.ink3}>Welcome to Lemma</Text>
                        </View>
                    ) : (
                        <View style={{ flexDirection: "row", alignItems: "center", marginRight: -12 }}>
                            <Text size={12.5} tone={color.ink3} style={{ flex: 1 }}>{at} of {count}</Text>
                            <IconButton name="close" size={18} label="End the tour" onPress={onClose} />
                        </View>
                    )}
                    <Text accessibilityRole="header" size={20} weight="medium" style={{ lineHeight: 25, letterSpacing: -0.2 }}>{stop.title}</Text>
                    <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{stop.line}</Text>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginTop: 6 }}>
                        {stop.id === "welcome" ? (
                            <>
                                <Button title="Not now" kind="outline" grow onPress={onClose} />
                                <Button title="Show me around" grow onPress={next} />
                            </>
                        ) : (
                            <>
                                <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ flexDirection: "row", gap: 5, flex: 1 }}>
                                    {stops.slice(1).map((one, index) => (
                                        <View key={one.id} style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: index + 1 === at ? color.ink : color.line2 }} />
                                    ))}
                                </View>
                                <Button title="Back" kind="quiet" onPress={back} />
                                <Button title={last ? "Done" : "Next"} onPress={next} />
                            </>
                        )}
                    </View>
                </View>
            </View>
        </Modal>
    );
}
