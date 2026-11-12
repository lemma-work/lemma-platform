import { useEffect, type ReactNode } from "react";
import { ActivityIndicator, ScrollView, Switch, TextInput, View, type TextInputProps } from "react-native";
import { useRouter } from "expo-router";
import Animated, { Easing, interpolate, ReduceMotion, useAnimatedStyle, useSharedValue, withRepeat, withTiming } from "react-native-reanimated";
import { stampOf } from "../../data/model";
import { Glyph } from "../../thread/glyphs";
import { LemmaFace, PersonFace } from "../../ui/face";
import { Icon, type IconName } from "../../ui/icons";
import { BREATH_MS } from "../../ui/motion";
import { Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import type { ChatEntry, ChatGlyph } from "./model";
import { faceIdOf, type RoomFace } from "./room";
import { sayTyping } from "@web/channels/live";
import type { PersonRef } from "@web/channels/waiting";

/** The small pieces the chats screens are drawn with: a chat's glyph, its
 *  one-line row, a face for whoever said something, a switch row, the
 *  segment chips, a search field. Tokens only, so every one holds in dark. */

const PLATFORM_ICON: Record<string, IconName> = {
    WHATSAPP: "whatsapp",
    TELEGRAM: "telegram",
    SLACK: "slack",
    RESEND: "mail",
    EMAIL: "mail",
    GMAIL: "mail",
    OUTLOOK: "mail",
};

/** What a chat is at a glance, the way the web marks it: "#" for a shared
 *  chat, a lock for a private one, the platform for a mirrored group, a bell
 *  for the reader's own notices, a contact's initials, a ring for a chat with
 *  the Lemma. */
export function ChatMark({ glyph, size = 16, width = 28 }: { glyph: ChatGlyph; size?: number; width?: number }) {
    const box = { width, alignItems: "center" as const, justifyContent: "center" as const };
    switch (glyph.kind) {
        case "hash": return <View style={box}><Text size={size} tone={color.ink2}>#</Text></View>;
        case "lock": return <View style={box}><Glyph name="lock" size={size} tone={color.ink2} /></View>;
        case "bell": return <View style={box}><Icon name="bell" size={size} tone={color.ink2} /></View>;
        case "automation": return <View style={box}><Icon name="automation" size={size} tone={color.ink2} /></View>;
        case "doc": return <View style={box}><Icon name="file" size={size} tone={color.ink2} /></View>;
        case "face": return <View style={box}><PersonFace name={glyph.name} size={Math.round(size * 1.35)} /></View>;
        case "platform": {
            if (glyph.platform.toUpperCase() === "WEB") return <View style={box}><Glyph name="globe" size={size} tone={color.ink2} /></View>;
            const icon = PLATFORM_ICON[glyph.platform.toUpperCase()];
            return <View style={box}>{icon ? <Icon name={icon} size={size} tone={color.ink2} /> : <Text size={size - 2} tone={color.ink2}>{glyph.platform.slice(0, 1)}</Text>}</View>;
        }
        default:
            return (
                <View style={box}>
                    <View style={{ width: 7, height: 7, borderRadius: 4, borderWidth: 1.5, borderColor: color.idleInk }} />
                </View>
            );
    }
}

/** A row's end: a violet dot when it needs the reader, an unread count, a
 *  violet ring while it works, else when it last moved. */
export function EntryEnd({ entry, now }: { entry: Pick<ChatEntry, "needsYou" | "unread" | "running" | "at">; now: number }) {
    if (entry.needsYou) return <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />;
    if (entry.unread > 0) {
        return (
            <View accessibilityLabel={`${entry.unread} new`} style={{ minWidth: 20, height: 20, paddingHorizontal: 6, borderRadius: 999, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                <Num size={12} tone={color.ink}>{entry.unread > 99 ? "99+" : entry.unread}</Num>
            </View>
        );
    }
    if (entry.running) {
        return (
            <View accessibilityLabel="Working" style={{ width: 13, height: 13, borderRadius: 7, backgroundColor: color.violetWash, alignItems: "center", justifyContent: "center" }}>
                <View style={{ width: 9, height: 9, borderRadius: 5, borderWidth: 2, borderColor: color.violet }} />
            </View>
        );
    }
    if (entry.at !== null) return <Num size={13} tone={color.ink3}>{stampOf(entry.at, now)}</Num>;
    return null;
}

/** One chat, on one dense line: its glyph, its name, its state. Holding it
 *  offers what the list can do to it (`onHold`). */
export function EntryRow({ entry, now, onHold, pad = 4 }: { entry: ChatEntry; now: number; onHold?: () => void; pad?: number }) {
    const router = useRouter();
    return (
        <Press
            role="link"
            label={entry.title + (entry.needsYou ? ", needs you" : entry.unread ? `, ${entry.unread} new` : entry.running ? ", working" : "")}
            onPress={() => router.push(entry.href as never)}
            onLongPress={onHold}
            delayLongPress={350}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: pad }}
        >
            <ChatMark glyph={entry.glyph} width={20} />
            <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                {entry.title}
                {entry.said ? <Text size={14} tone={color.ink3}>{"  " + entry.said}</Text> : null}
            </Text>
            <EntryEnd entry={entry} now={now} />
        </Press>
    );
}

/** Whoever said something: a person's disc, a contact's, the Lemma's own
 *  character -- or, for another of its agents, a character of its own. */
export function SaidBy({ person, lemma, frontId, size = 30 }: { person: PersonRef; lemma: { id: string; iconUrl: string | null } | null; frontId: string | null; size?: number }) {
    const faceId = faceIdOf(person, lemma, frontId);
    if (faceId && lemma) return <LemmaFace id={faceId} icon={faceId === lemma.id ? lemma.iconUrl : null} size={size} />;
    return <PersonFace name={person.name ?? "?"} size={size} />;
}

/** Who is in a room, as faces laid over each other by 7px: a Lemma as its
 *  character standing free, a person as a disc ringed in the paper so the
 *  next one reads behind it; the first on top, and a dot on whoever has the
 *  app open. */
export function FaceStack({ faces, more, lemma, frontId, online, size = 26 }: {
    faces: readonly RoomFace[];
    more: number;
    lemma: { id: string; iconUrl: string | null } | null;
    frontId: string | null;
    online: ReadonlySet<string>;
    size?: number;
}) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center" }}>
            {faces.map((face, index) => {
                const here = face.person.kind === "MEMBER" && face.person.id ? online.has(face.person.id) : false;
                return (
                    <View key={face.key} style={face.lemma
                        ? { marginLeft: index === 0 ? 0 : -7, zIndex: faces.length - index, padding: 2 }
                        : { marginLeft: index === 0 ? 0 : -7, zIndex: faces.length - index, borderRadius: (size + 4) / 2, borderWidth: 2, borderColor: color.paper, backgroundColor: color.paper }}>
                        <SaidBy person={face.person} lemma={lemma} frontId={frontId} size={size} />
                        {here ? <View style={{ position: "absolute", right: -1, bottom: -1, width: 8, height: 8, borderRadius: 4, backgroundColor: color.ink, borderWidth: 1.5, borderColor: color.paper }} /> : null}
                    </View>
                );
            })}
            {more > 0 ? (
                <View style={{ marginLeft: -7, height: size + 4, minWidth: size + 4, paddingHorizontal: 5, borderRadius: (size + 4) / 2, borderWidth: 2, borderColor: color.paper, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                    <Num size={11.5} tone={color.ink2}>+{more}</Num>
                </View>
            ) : null}
        </View>
    );
}

/** The platform somebody wrote in from, as a small glyph beside their name. */
export function PlatformMark({ platform, size = 13 }: { platform: string; size?: number }) {
    const icon = PLATFORM_ICON[platform.toUpperCase()];
    return icon ? <Icon name={icon} size={size} tone={color.ink3} /> : null;
}

/** A small dot that breathes, for something going on now: someone typing,
 *  a Lemma answering. Still under the phone's reduce-motion setting. */
export function BreathingDot({ tone, size = 7 }: { tone: string; size?: number }) {
    const breath = useSharedValue(0);
    useEffect(() => {
        breath.value = withRepeat(withTiming(1, { duration: BREATH_MS / 2, easing: Easing.inOut(Easing.sin), reduceMotion: ReduceMotion.System }), -1, true);
    }, [breath]);
    const style = useAnimatedStyle(() => ({ opacity: interpolate(breath.value, [0, 1], [0.35, 1]), transform: [{ scale: interpolate(breath.value, [0, 1], [0.8, 1.1]) }] }));
    return <Animated.View style={[{ width: size, height: size, borderRadius: size / 2, backgroundColor: tone }, style]} />;
}

/** A small grey label over a group of rows. */
export function Label({ children, right }: { children: string; right?: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", marginTop: 14, marginBottom: 2, paddingHorizontal: 4, minHeight: 24 }}>
            <Text size={12.5} tone={color.ink3} style={{ flex: 1 }}>{children}</Text>
            {right}
        </View>
    );
}

/** A quiet link-like action on a row of its own. */
export function TextAction({ label, onPress, tone = color.ink2, busy = false, disabled = false }: { label: string; onPress: () => void; tone?: string; busy?: boolean; disabled?: boolean }) {
    return (
        <Press label={label} disabled={disabled || busy} onPress={onPress} style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 4, alignSelf: "flex-start", opacity: disabled ? 0.45 : 1 }}>
            {busy ? <ActivityIndicator size="small" color={color.ink3} /> : <Text size={14.5} tone={tone}>{label}</Text>}
        </Press>
    );
}

/** Chips to pick one of a few, as the Needs you filters are. */
export function Segments<T extends string>({ items, chosen, onPick, label }: { items: readonly { id: T; label: string; count?: number | null }[]; chosen: T; onPick: (id: T) => void; label: string }) {
    /* One line that scrolls sideways, as the Needs you filters do, rather
       than chips wrapping onto a second. */
    return (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} accessibilityRole="tablist" accessibilityLabel={label} contentContainerStyle={{ gap: 8 }} style={{ flexGrow: 0 }} keyboardShouldPersistTaps="handled">
            {items.map((one) => {
                const on = one.id === chosen;
                return (
                    <Press key={one.id} role="tab" label={one.label} accessibilityState={{ selected: on }} onPress={() => onPick(one.id)} style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: on ? color.ink : "transparent", borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
                        <Text size={14} tone={on ? color.onInk : color.ink}>{one.label}</Text>
                        {one.count ? <Num size={12.5} tone={on ? color.onInk : color.ink3}>{one.count}</Num> : null}
                    </Press>
                );
            })}
        </ScrollView>
    );
}

/** A switch with what it does, and a sentence under it. */
export function SwitchRow({ label, note, value, onChange, disabled = false }: { label: string; note?: string | null; value: boolean; onChange: (on: boolean) => void; disabled?: boolean }) {
    return (
        <View style={{ gap: 4, paddingVertical: 6 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH }}>
                <Text size={15.5} style={{ flex: 1 }}>{label}</Text>
                <Switch
                    accessibilityLabel={label}
                    value={value}
                    disabled={disabled}
                    onValueChange={onChange}
                    trackColor={{ false: color.track, true: color.ink }}
                    thumbColor={value ? color.paper : color.ink3}
                    ios_backgroundColor={color.track}
                />
            </View>
            {note ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{note}</Text> : null}
        </View>
    );
}

/** A rule between groups of rows. */
export function Rule() {
    return <View style={{ height: 1, backgroundColor: color.line, marginVertical: 8 }} />;
}

/** A box to type in, the app's field. */
export function Field({ style, ...rest }: TextInputProps) {
    return (
        <TextInput
            placeholderTextColor={color.ink3}
            {...rest}
            style={[{ minHeight: TOUCH, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, fontFamily: font.regular, fontSize: 16, color: color.ink }, style]}
        />
    );
}

/** The search box at the top of a list. */
export function SearchField({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: 42, borderRadius: 12, backgroundColor: color.wash, paddingHorizontal: 12 }}>
            <Icon name="search" size={17} tone={color.ink3} />
            <TextInput
                accessibilityLabel={placeholder}
                value={value}
                onChangeText={onChange}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                returnKeyType="search"
                autoCorrect={false}
                style={{ flex: 1, fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0 }}
            />
            {value ? (
                <Press label="Clear the search" onPress={() => onChange("")} style={{ width: 28, height: 28, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={14} tone={color.ink3} width={2} />
                </Press>
            ) : null}
        </View>
    );
}

/** Something that went wrong, said where it happened, in amber. */
export function Trouble({ children }: { children: string | null }) {
    if (!children) return null;
    return <Text accessibilityRole="alert" size={13.5} tone={color.amber} style={{ lineHeight: 19 }}>{children}</Text>;
}

/** A choice of one on a row: the options as chips. */
export function Choices<T extends string>({ options, value, onPick, disabled = false, label }: { options: readonly { value: T; label: string }[]; value: T; onPick: (value: T) => void; disabled?: boolean; label: string }) {
    return (
        <View accessibilityRole="radiogroup" accessibilityLabel={label} style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {options.map((one) => {
                const on = one.value === value;
                return (
                    <Press key={one.value} role="radio" label={one.label} disabled={disabled} accessibilityState={{ checked: on, disabled }} onPress={() => onPick(one.value)} style={{ minHeight: 34, paddingHorizontal: 12, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : "transparent", borderWidth: on ? 0 : 1, borderColor: color.line2, opacity: disabled && !on ? 0.5 : 1 }}>
                        <Text size={14} tone={on ? color.onInk : color.ink}>{one.label}</Text>
                    </Press>
                );
            })}
        </View>
    );
}

/** Who is typing, above the box, with a dot that breathes while they are. */
export function Typing({ names }: { names: readonly string[] }) {
    if (!names.length) return null;
    return (
        <View accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 20, paddingTop: 6, paddingBottom: 2 }}>
            <BreathingDot tone={color.ink3} />
            <Text size={12.5} tone={color.ink2} numberOfLines={1} style={{ flexShrink: 1 }}>{sayTyping(names)}</Text>
        </View>
    );
}
