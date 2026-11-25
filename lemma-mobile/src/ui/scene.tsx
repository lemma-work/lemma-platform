import { useEffect } from "react";
import { View } from "react-native";
import Animated, { Easing, ReduceMotion, useAnimatedStyle, useSharedValue, withRepeat, withSequence, withTiming } from "react-native-reanimated";
import { MAX_SLOTS, PLACE_ANGLES, YOU_ANGLE, slotAngles } from "@web/arrival/stage-model";
import type { AppItem, Lemma } from "../data/types";
import { AppMark } from "./app-mark";
import { LemmaFace, initialsOf } from "./face";
import { haptics } from "./haptics";
import { Icon, type IconName } from "./icons";
import { BREATH_MS } from "./motion";
import { Press } from "./press";
import { Num, Text } from "./text";
import { color } from "./tokens";

/** A Lemma's scene, as the web draws it on the way in and on a Lemma's own
 *  page (`arrival/stage.tsx`, `outcomes/lemma-page.tsx`): its face on its
 *  tint, its apps as round spots over its head, the places it can be
 *  reached docked at its feet, and you beside it. Every one of them is a
 *  button (#1228): an app opens, the empty spot adds one, a place opens
 *  where you can reach it, you open its people.
 *
 *  Angles and radii are the web's (`stage-model.ts`, `arrival.css`): spots
 *  on a circle 43% of the stage out, places 35.5%. */

export type PlaceKey = "EMAIL" | "WHATSAPP" | "TELEGRAM" | "SLACK";

export interface ScenePlace {
    key: PlaceKey;
    /** Turned on: drawn solid. Off: a dashed ghost that says "Add …". */
    on: boolean;
}

const PLACE_ICON: Record<PlaceKey, IconName> = { EMAIL: "mail", WHATSAPP: "whatsapp", TELEGRAM: "telegram", SLACK: "slack" };
const PLACE_NAME: Record<PlaceKey, string> = { EMAIL: "Email", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack" };

function at(angle: number, radius: number, centre: number, size: number) {
    const rad = (angle * Math.PI) / 180;
    return { left: centre + Math.cos(rad) * radius - size / 2, top: centre + Math.sin(rad) * radius - size / 2 };
}

export function Scene({ lemma, apps, places, people, me, size = 240, still = false, onApp, onAddApp, onPlace, onPeople }: {
    lemma: Pick<Lemma, "id" | "iconUrl" | "name">;
    apps: readonly AppItem[];
    places: readonly ScenePlace[];
    /** How many people are in it, you counted. */
    people: number;
    /** Your name, for your bubble. */
    me: string;
    size?: number;
    /** The still face, for a page that is not about this one Lemma. */
    still?: boolean;
    onApp?: (app: AppItem) => void;
    onAddApp?: () => void;
    onPlace?: (place: ScenePlace) => void;
    onPeople?: () => void;
}) {
    const centre = size / 2;
    const face = Math.round(size * 0.56);
    const spot = Math.round(size * 0.2);
    const badge = Math.round(size * 0.15);
    const shown = apps.slice(0, MAX_SLOTS);
    /* While there is room, the last spot is the one that adds an app. */
    const withAdd = shown.length < MAX_SLOTS && onAddApp;
    const angles = slotAngles(shown.length + (withAdd ? 1 : 0));
    const breath = useSharedValue(0);
    useEffect(() => {
        if (still) return;
        breath.value = withRepeat(
            withSequence(
                withTiming(1, { duration: BREATH_MS * 1.3, easing: Easing.inOut(Easing.sin), reduceMotion: ReduceMotion.System }),
                withTiming(0, { duration: BREATH_MS * 1.3, easing: Easing.inOut(Easing.sin), reduceMotion: ReduceMotion.System }),
            ),
            -1,
        );
    }, [breath, still]);
    const breathing = useAnimatedStyle(() => ({ transform: [{ translateY: -3 * breath.value }, { scale: 1 + 0.012 * breath.value }] }));
    const others = Math.max(0, people - 1);

    return (
        <View style={{ width: size, height: size }}>
            {/* The glow the face stands in. */}
            <View pointerEvents="none" style={{ position: "absolute", left: size * 0.12, top: size * 0.12, width: size * 0.76, height: size * 0.76, borderRadius: size, backgroundColor: color.tint, opacity: 0.55 }} />
            <Animated.View
                style={[{
                    position: "absolute", left: (size - face) / 2, top: (size - face) / 2, width: face, height: face, borderRadius: face * 0.3,
                    shadowColor: "#3c2878", shadowOpacity: 0.18, shadowRadius: 24, shadowOffset: { width: 0, height: 14 }, elevation: 6,
                }, breathing]}
            >
                <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={face} />
            </Animated.View>

            {shown.map((app, index) => (
                <Press
                    key={app.id}
                    label={`Open ${app.label}`}
                    onPress={() => { haptics.tick(); onApp?.(app); }}
                    style={{ position: "absolute", ...at(angles[index], size * 0.43, centre, spot) }}
                >
                    <AppMark name={app.name} label={app.label} size={spot} shadow />
                </Press>
            ))}
            {withAdd ? (
                <Press
                    label="Add an app"
                    onPress={() => { haptics.tick(); onAddApp?.(); }}
                    style={{
                        position: "absolute", ...at(angles[shown.length], size * 0.43, centre, spot),
                        width: spot, height: spot, borderRadius: spot / 2, borderWidth: 1.5, borderStyle: "dashed", borderColor: color.line2,
                        alignItems: "center", justifyContent: "center",
                    }}
                >
                    <Icon name="plus" size={Math.round(spot * 0.42)} tone={color.ink3} />
                </Press>
            ) : null}

            {places.map((place) => (
                <Press
                    key={place.key}
                    label={place.on ? PLACE_NAME[place.key] : `Add ${PLACE_NAME[place.key]}`}
                    onPress={() => { haptics.tick(); onPlace?.(place); }}
                    style={{
                        position: "absolute", ...at(PLACE_ANGLES[place.key], size * 0.355, centre, badge),
                        width: badge, height: badge, borderRadius: badge / 2, alignItems: "center", justifyContent: "center",
                        ...(place.on
                            ? { backgroundColor: color.paper, borderWidth: 3, borderColor: color.paper, shadowColor: "#000", shadowOpacity: 0.1, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 }
                            : { borderWidth: 1.5, borderStyle: "dashed", borderColor: color.line2, opacity: 0.8 }),
                    }}
                >
                    <Icon name={PLACE_ICON[place.key]} size={Math.round(badge * 0.46)} tone={place.on ? color.ink : color.ink3} />
                </Press>
            ))}

            <Press
                label={others > 0 ? `People: you and ${others} more` : "People: just you"}
                onPress={() => { haptics.tick(); onPeople?.(); }}
                style={{
                    position: "absolute", ...at(YOU_ANGLE, size * 0.43, centre, badge),
                    width: badge, height: badge, borderRadius: badge / 2, backgroundColor: color.wash, borderWidth: 2, borderColor: color.paper,
                    alignItems: "center", justifyContent: "center",
                    shadowColor: "#000", shadowOpacity: 0.1, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3,
                }}
            >
                <Text size={Math.round(badge * 0.36)}>{initialsOf(me).charAt(0)}</Text>
                {others > 0 ? (
                    <View style={{ position: "absolute", right: -9, bottom: -7, height: 18, paddingHorizontal: 5, borderRadius: 9, backgroundColor: color.paper, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: color.line2 }}>
                        <Num size={10.5} tone={color.ink2}>{`+${others}`}</Num>
                    </View>
                ) : null}
            </Press>
        </View>
    );
}
