import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, KeyboardAvoidingView, Linking, Platform, ScrollView, View } from "react-native";
import type { UserResponse } from "lemma-sdk";
import { useSession } from "../../../session/session";
import { BackLink } from "../../../ui/chrome";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useProfile, useSaveProfile, useVerifyConfig, useWhatsAppVerification } from "../../admin/account/hooks";
import { clock } from "../../admin/account/verify";
import { changes, draftOf, localTimezone } from "@web/session/profile-edit";
import { sameZone, supportedZones, zoneChoices, zoneCity, zoneMatches, zoneRegion } from "../../admin/account/zones";
import { Field, Group, Row, Say, problemOf } from "../../admin/ui";
import { readSettled, writeSettled } from "./settled";
import { asksFirstProfile, canDismissFirstProfile, firstProfileNeeds, needsTimezoneSync, syncDetectedTimezone } from "@web/session/first-profile";

/** Sample data asks once per launch, in memory, so the step can be looked
 *  at without a new account. */
let askedThisLaunch = false;

/** The one step between signing up and the app: your name, your time zone,
 *  and your phone (the web's `FirstProfileStep`). Mount it once, on Home.
 *
 *  The web's rules (`web/first-profile.ts`): only a new account, only when
 *  something the step can supply is missing, and never again once it was
 *  continued or skipped on this phone. Decided once, when the answers are
 *  in, and then held, because verifying the phone removes the step's own
 *  reason to exist and a sheet that re-derived itself would vanish the
 *  moment it worked. A new account that never sees it still gets the
 *  phone's time zone written, in the background. */
export function FirstProfileStep({ hold = false, onShown }: {
    /** Not on the visit an install opens: that person came to use what
     *  they installed, and is asked on their next visit instead. */
    hold?: boolean;
    /** Told once it has decided, and again when the sheet goes away, so the
     *  tour waits for it rather than opening over it (`HomeOverlays`). */
    onShown?: (shown: boolean) => void;
}) {
    const { mode } = useSession();
    const sample = mode === "sample";
    const profile = useProfile();
    const verify = useVerifyConfig();
    const background = useSaveProfile();
    const userId = profile.data?.id ?? null;

    const [settled, setSettled] = useState<boolean | null>(null);
    useEffect(() => {
        if (sample || !userId) return;
        let current = true;
        void readSettled(userId).then((held) => { if (current) setSettled(held); });
        return () => { current = false; };
    }, [sample, userId]);

    const [decision, setDecision] = useState<{ open: boolean; askPhone: boolean } | null>(null);
    const ready = profile.data !== undefined && !verify.isPending && (sample || settled !== null);
    if (decision === null && ready && profile.data) {
        const whatsApp = verify.data?.available === true;
        setDecision({
            open: !hold && (sample ? !askedThisLaunch : asksFirstProfile(profile.data, { whatsApp, settled: settled === true })),
            askPhone: firstProfileNeeds(profile.data, { whatsApp }).phone,
        });
    }
    useEffect(() => {
        if (sample && decision?.open) askedThisLaunch = true;
    }, [sample, decision]);
    const told = useRef(onShown);
    told.current = onShown;
    useEffect(() => {
        if (decision) told.current?.(decision.open);
    }, [decision]);

    /* An account that already has a name (a provider supplied one) never
       sees the sheet, and until something writes a zone every "when" it is
       told is in the wrong one. `needsTimezoneSync` owns who qualifies. */
    const save = useRef(background.mutateAsync);
    save.current = background.mutateAsync;
    const synced = useRef(false);
    useEffect(() => {
        if (!decision || !profile.data || synced.current) return;
        if (!needsTimezoneSync(profile.data, { dialogOpen: decision.open })) return;
        const zone = localTimezone();
        if (!zone) return;
        synced.current = true;
        const stop = syncDetectedTimezone(() => save.current({ timezone: zone }), () => undefined, () => { synced.current = false; });
        return () => {
            stop();
            synced.current = false;
        };
    }, [decision, profile.data]);

    if (!decision?.open || !profile.data) return null;
    return (
        <FirstProfileSheet
            user={profile.data}
            askPhone={decision.askPhone}
            onDone={() => {
                if (!sample && userId) void writeSettled(userId);
                setDecision({ ...decision, open: false });
            }}
        />
    );
}

function FirstProfileSheet({ user, askPhone, onDone }: { user: UserResponse; askPhone: boolean; onDone: () => void }) {
    const save = useSaveProfile();
    const [first, setFirst] = useState(() => (user.first_name ?? "").trim());
    const [last, setLast] = useState(() => (user.last_name ?? "").trim());
    const [timezone, setTimezone] = useState(() => (user.timezone ?? "").trim() || localTimezone());
    const [picking, setPicking] = useState(false);
    const canDismiss = canDismissFirstProfile(save.isPending);
    const ready = first.trim().length > 0 && timezone.length > 0;

    const submit = () => {
        if (!ready || save.isPending) return;
        /* Against the person as they are now: verifying the phone rewrites
           them, and only the name and the zone are this sheet's to change. */
        const before = draftOf(user);
        const patch = changes(before, { ...before, first_name: first, last_name: last, timezone });
        if (Object.keys(patch).length === 0) {
            onDone();
            return;
        }
        save.mutate(patch, { onSuccess: onDone });
    };
    const edit = (set: (next: string) => void) => (next: string) => {
        save.reset();
        set(next);
    };

    return (
        <Sheet open onClose={() => { if (canDismiss) onDone(); }} tall>
            {picking ? (
                <ZonePicker held={timezone} onBack={() => setPicking(false)} onPick={(zone) => { save.reset(); setTimezone(zone); setPicking(false); }} />
            ) : (
                <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flexShrink: 1 }}>
                    <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 6, gap: 18 }} keyboardShouldPersistTaps="handled">
                        <View style={{ gap: 4 }}>
                            <Text accessibilityRole="header" size={20} weight="medium">Before you start</Text>
                            <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>Your Lemmas call you by this name.</Text>
                        </View>
                        <View style={{ gap: 12 }}>
                            <Field label="First name" value={first} onChange={edit(setFirst)} autoCapitalize="words" />
                            <Field label="Last name" value={last} onChange={edit(setLast)} autoCapitalize="words" />
                        </View>
                        <Group label="Time zone">
                            <Row
                                label={timezone ? zoneCity(timezone) : "Not set"}
                                hint={timezone ? zoneRegion(timezone) || null : "Pick the zone you live in"}
                                value={timezone && sameZone(timezone, localTimezone()) ? "Where this phone is" : null}
                                onPress={() => setPicking(true)}
                                last
                            />
                        </Group>
                        {askPhone ? <PhoneByWhatsApp /> : null}
                        {save.isError ? <Say tone="bad">{`That did not save. ${problemOf(save.error, "Try again.")}`}</Say> : null}
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Skip for now" kind="outline" grow height={48} disabled={!canDismiss} onPress={onDone} />
                            <Button title={save.isPending ? "Saving…" : "Continue"} grow height={48} busy={save.isPending} disabled={!ready} onPress={submit} />
                        </View>
                    </ScrollView>
                </KeyboardAvoidingView>
            )}
        </Sheet>
    );
}

/** Every zone, searchable, the phone's own first and the held one ticked,
 *  in the same sheet rather than one over it. */
function ZonePicker({ held, onPick, onBack }: { held: string; onPick: (zone: string) => void; onBack: () => void }) {
    const here = localTimezone();
    const [query, setQuery] = useState("");
    const all = useMemo(() => zoneChoices(here, held, supportedZones()), [here, held]);
    const shown = all.filter((zone) => zoneMatches(zone, query));
    return (
        <>
            <View style={{ flexDirection: "row", paddingHorizontal: 8 }}>
                <BackLink label="Before you start" onPress={onBack} />
            </View>
            <View style={{ paddingHorizontal: 20, paddingBottom: 6 }}>
                <Field value={query} onChange={setQuery} placeholder="Search a city or region" autoCapitalize="none" />
            </View>
            <FlatList
                data={shown}
                keyExtractor={(zone) => zone}
                keyboardShouldPersistTaps="handled"
                contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8 }}
                ListEmptyComponent={<Text size={14.5} tone={color.ink3} style={{ paddingVertical: 14 }}>No zone by that name.</Text>}
                renderItem={({ item, index }) => {
                    const on = item === held;
                    const region = zoneRegion(item);
                    const hint = sameZone(item, here) ? (region ? `${region} · where this phone is` : "Where this phone is") : region || null;
                    return (
                        <Press
                            role="radio"
                            label={sameZone(item, here) ? `${zoneCity(item)}, where this phone is` : zoneCity(item)}
                            accessibilityState={{ selected: on }}
                            onPress={() => onPick(item)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: index === shown.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <View style={{ flex: 1, gap: 1 }}>
                                <Text size={16}>{zoneCity(item)}</Text>
                                {hint ? <Text size={13} tone={color.ink3}>{hint}</Text> : null}
                            </View>
                            {on ? <Icon name="check" size={18} width={2} /> : null}
                        </Press>
                    );
                }}
            />
        </>
    );
}

/** The phone, proved rather than typed. A code is started as soon as this
 *  is on screen, once and never again on its own: five starts in fifteen
 *  minutes is the whole allowance. Whichever phone sends the message is the
 *  number bound, so on this phone it is one tap and one send. */
function PhoneByWhatsApp() {
    const [verified, setVerified] = useState<string | null>(null);
    const wa = useWhatsAppVerification((next) => setVerified((next.mobile_number ?? "").trim() || null));
    const started = useRef(false);
    const start = wa.start;
    useEffect(() => {
        if (started.current) return;
        started.current = true;
        void start();
    }, [start]);

    return (
        <Group
            label="Your phone, optional"
            right={wa.transaction && !verified ? (
                <View accessibilityLabel={`${Math.ceil(wa.seconds / 60)} minutes left`} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                    <Icon name="clock" size={13} tone={color.ink3} />
                    <Num size={13} tone={color.ink3}>{clock(wa.seconds)}</Num>
                </View>
            ) : null}
        >
            {verified ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 4 }}>
                    <Icon name="check" size={16} width={2} />
                    <Say tone="done">{`${verified} is verified as yours.`}</Say>
                </View>
            ) : wa.transaction ? (
                <View style={{ gap: 8, paddingTop: 4 }}>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>Prove it’s yours, so any Lemma you put on WhatsApp later knows it’s you.</Text>
                    <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>
                        It sends <Num size={13.5} tone={color.ink2}>{wa.message}</Num> to <Num size={13.5} tone={color.ink2}>{wa.transaction.displayNumber}</Num>, Lemma’s verification number.
                    </Text>
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Open WhatsApp" kind="outline" onPress={() => void Linking.openURL(wa.transaction!.whatsappUrl).catch(() => undefined)} />
                    </View>
                </View>
            ) : wa.error ? (
                <View style={{ gap: 8, paddingTop: 4 }}>
                    <Say tone="bad">{wa.error}</Say>
                    <View style={{ flexDirection: "row" }}>
                        <Button title={wa.starting ? "Preparing…" : "Get a new code"} kind="outline" busy={wa.starting} onPress={() => void wa.start()} />
                    </View>
                </View>
            ) : (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 6 }}>
                    <ActivityIndicator size="small" color={color.ink3} />
                    <Text size={13.5} tone={color.ink3}>Preparing a code…</Text>
                </View>
            )}
        </Group>
    );
}
