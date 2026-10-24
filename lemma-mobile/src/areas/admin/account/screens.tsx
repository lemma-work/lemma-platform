import { useEffect, useMemo, useState } from "react";
import { FlatList, Platform, View } from "react-native";
import DateTimePicker, { DateTimePickerAndroid, type DateTimePickerEvent } from "@react-native-community/datetimepicker";
import type { UserResponse } from "lemma-sdk";
import { Loading } from "../../../ui/chrome";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { settingsEntry } from "../sections";
import { Field, Group, ReadProblem, Row, Say, SettingsScreen, problemOf } from "../ui";
import { useProfile, useSaveProfile } from "./hooks";
import { VerifyMobile } from "./verify-mobile";
import { isVerifiedNumber, phoneProblems } from "./verify";
import { dayToDate, isoDay, sameZone, spokenDay, supportedZones, zoneChoices, zoneCity, zoneMatches, zoneRegion } from "./zones";
import { changes, draftOf, hasChanges, localTimezone, problems, type ProfileDraft } from "@web/session/profile-edit";

const ENTRY = settingsEntry("profile");

/** Your profile, edited here (the web's `session/profile-panel.tsx`): your
 *  name, the time zone every "when" is told in, where you are, and your
 *  mobile number with its proof over WhatsApp. The address you sign in with
 *  is the server's answer, not something to change here. */
export function ProfileScreen() {
    const profile = useProfile();
    return (
        <SettingsScreen title={ENTRY?.title ?? "Profile"} note={ENTRY?.hint} back="Account">
            {profile.isPending ? <Loading label="Reading your profile" /> : profile.isError ? (
                <ReadProblem said={problemOf(profile.error, "Your profile could not be read.")} onRetry={() => void profile.refetch()} />
            ) : (
                <ProfileForm user={profile.data} />
            )}
        </SettingsScreen>
    );
}

function ProfileForm({ user }: { user: UserResponse }) {
    const server = useMemo(() => draftOf(user), [user]);
    const [draft, setDraft] = useState<ProfileDraft>(server);
    /* What the form last took from the server: a profile that changed
       elsewhere moves the form along only where nothing has been typed. */
    const [base, setBase] = useState<ProfileDraft>(server);
    const [saved, setSaved] = useState(false);
    const [zones, setZones] = useState(false);
    const save = useSaveProfile();

    useEffect(() => {
        setDraft((current) => (hasChanges(base, current) ? current : server));
        setBase(server);
    }, [server]);

    const here = localTimezone();
    const wrong = phoneProblems(problems(draft));
    const dirty = hasChanges(server, draft);
    const verified = isVerifiedNumber(user, draft.mobile_number);

    const set = (field: keyof ProfileDraft, value: string) => {
        setSaved(false);
        save.reset();
        setDraft((current) => ({ ...current, [field]: value }));
    };

    return (
        <>
            <Group label="Signed in as" foot="The address you sign in with. It cannot be changed here.">
                <Row label={user.email} last />
            </Group>

            <Group label="Name">
                <View style={{ gap: 12, paddingTop: 4 }}>
                    <Field label="First name" value={draft.first_name} onChange={(next) => set("first_name", next)} autoCapitalize="words" />
                    <Field label="Last name" value={draft.last_name} onChange={(next) => set("last_name", next)} autoCapitalize="words" />
                </View>
            </Group>

            <Group label="Time zone" foot="Lemma tells you when things happen in this zone: when a run finished, when standing work starts.">
                <Row
                    label={draft.timezone ? zoneCity(draft.timezone) : "Not set"}
                    hint={draft.timezone ? zoneRegion(draft.timezone) || null : "Pick the zone you live in"}
                    onPress={() => setZones(true)}
                    last
                />
                {wrong.timezone ? <Say tone="bad">{wrong.timezone}</Say> : null}
                {here && !sameZone(draft.timezone, here) ? (
                    <Press label={`Use ${here}, where this phone is`} onPress={() => set("timezone", here)} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                        <Text size={14.5} weight="medium">Use {zoneCity(here)}, where this phone is</Text>
                    </Press>
                ) : null}
            </Group>

            <Group label="About you">
                <View style={{ gap: 12, paddingTop: 4 }}>
                    <Field label="Country" value={draft.country} onChange={(next) => set("country", next)} autoCapitalize="words" />
                </View>
                <BirthDay value={draft.date_of_birth} problem={wrong.date_of_birth ?? null} onChange={(next) => set("date_of_birth", next)} />
            </Group>

            <Group label="Mobile number" foot="A WhatsApp message from this number is known to be you.">
                <View style={{ gap: 10, paddingTop: 4 }}>
                    <Field label="Number, starting with its country code" value={draft.mobile_number} onChange={(next) => set("mobile_number", next)} keyboardType="phone-pad" placeholder="+44 7700 900123" />
                    {verified ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                            <Icon name="check" size={16} width={2} />
                            <Say tone="done">This number is verified.</Say>
                        </View>
                    ) : (
                        <VerifyMobile
                            number={draft.mobile_number}
                            onVerified={(next) => {
                                /* The server wrote the number as it verified it.
                                   Only that box is told: the rest may be mid-edit. */
                                setSaved(false);
                                save.reset();
                                setDraft((current) => ({ ...current, mobile_number: (next.mobile_number ?? "").trim() }));
                            }}
                        />
                    )}
                </View>
            </Group>

            <View style={{ gap: 10 }}>
                {save.isError ? <Say tone="bad">{`That did not save. ${problemOf(save.error, "Try again.")}`}</Say> : null}
                {saved && !dirty ? <Say tone="done">Saved.</Say> : null}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Discard" kind="outline" grow disabled={!dirty || save.isPending} onPress={() => { setDraft(server); save.reset(); }} />
                    <Button
                        title={save.isPending ? "Saving…" : "Save changes"}
                        grow
                        busy={save.isPending}
                        disabled={!dirty || Object.keys(wrong).length > 0}
                        onPress={() => save.mutate(changes(server, draft), { onSuccess: () => setSaved(true) })}
                    />
                </View>
            </View>

            <ZoneSheet open={zones} held={draft.timezone} here={here} onClose={() => setZones(false)} onPick={(zone) => { set("timezone", zone); setZones(false); }} />
        </>
    );
}

/** Every zone, searchable, the phone's own first and the held one ticked. */
function ZoneSheet({ open, held, here, onPick, onClose }: { open: boolean; held: string; here: string; onPick: (zone: string) => void; onClose: () => void }) {
    const [query, setQuery] = useState("");
    const all = useMemo(() => zoneChoices(here, held, supportedZones()), [here, held]);
    const shown = all.filter((zone) => zoneMatches(zone, query));
    return (
        <Sheet open={open} onClose={() => { setQuery(""); onClose(); }} title="Time zone" tall>
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
                            label={item === here ? `${zoneCity(item)}, where this phone is` : zoneCity(item)}
                            accessibilityState={{ selected: on }}
                            onPress={() => { setQuery(""); onPick(item); }}
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
        </Sheet>
    );
}

/** Optional, and a date: picked, never typed. */
function BirthDay({ value, problem, onChange }: { value: string; problem: string | null; onChange: (next: string) => void }) {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState<Date>(() => dayToDate(value) ?? new Date(1990, 0, 1, 12));
    const today = new Date();
    const pick = () => {
        const start = dayToDate(value) ?? new Date(1990, 0, 1, 12);
        if (Platform.OS === "android") {
            DateTimePickerAndroid.open({
                value: start,
                mode: "date",
                maximumDate: today,
                onChange: (event: DateTimePickerEvent, date?: Date) => {
                    if (event.type === "set" && date) onChange(isoDay(date));
                },
            });
            return;
        }
        setDraft(start);
        setOpen(true);
    };
    return (
        <>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingTop: 8 }}>
                <View style={{ flex: 1 }}>
                    <Row label="Date of birth" hint="Optional" value={spokenDay(value) || "Not set"} onPress={pick} last />
                </View>
                {value ? (
                    <Press label="Clear the date of birth" onPress={() => onChange("")} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                        <Icon name="close" size={16} tone={color.ink3} />
                    </Press>
                ) : null}
            </View>
            {problem ? <Text size={13} tone={color.amber}>{problem}</Text> : null}
            {Platform.OS === "ios" ? (
                <Sheet open={open} onClose={() => setOpen(false)} title="Date of birth">
                    <View style={{ alignItems: "center", paddingHorizontal: 12 }}>
                        <DateTimePicker value={draft} mode="date" display="inline" maximumDate={today} onChange={(_, date) => date && setDraft(date)} />
                    </View>
                    <View style={{ paddingHorizontal: 20, paddingTop: 8 }}>
                        <Button title="Done" height={48} onPress={() => { onChange(isoDay(draft)); setOpen(false); }} />
                    </View>
                </Sheet>
            ) : null}
        </>
    );
}
