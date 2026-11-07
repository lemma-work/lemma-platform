import { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { pickSquarePhoto, type PickedFile } from "../../../data/io";
import { routes } from "../../../data/routes";
import { Loading } from "../../../ui/chrome";
import { CAST, LemmaFace, characterForSeed, type CharacterName } from "../../../ui/face";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Image } from "expo-image";
import { useMayI } from "../access/hooks";
import type { PartProps } from "../types";
import { Confirm, Field, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { DELETE_POD } from "@web/data/pod-roles";
import { identityIcon, variantFor } from "./faces";
import { useDeleteLemma, useSaveAbout, useSetFace } from "./hooks";

/** A Lemma's name, what it is for, and its face: the web's About hero with
 *  its Edit, as a screen of its own. */
export function EditAboutScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const router = useRouter();
    const [name, setName] = useState(lemma?.name ?? "");
    const [job, setJob] = useState(lemma?.description ?? "");
    const [faces, setFaces] = useState(false);
    /* Filled once the Lemma is read, never over what is being typed. */
    const [filled, setFilled] = useState(Boolean(lemma));
    useEffect(() => {
        if (lemma && !filled) { setName(lemma.name); setJob(lemma.description ?? ""); setFilled(true); }
    }, [lemma, filled]);
    const save = useSaveAbout(lemma ?? { id: podId, name: "", iconUrl: null, description: null });
    const face = useSetFace(podId);

    if (!lemma) return <SettingsScreen title="Name and face" back="Settings"><Loading /></SettingsScreen>;
    const changed = name.trim() !== lemma.name || job.trim() !== (lemma.description ?? "").trim();
    return (
        <SettingsScreen title="Name and face" note={`How ${lemma.name} looks and what it is for, for everyone here.`} back={lemma.name}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 16 }}>
                <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={72} />
                <View style={{ flex: 1, gap: 4 }}>
                    <Press label="Change the face" onPress={() => setFaces(true)} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                        <Text size={15.5} weight="medium">Change the face</Text>
                    </Press>
                    {face.isPending ? <Say>Saving the face…</Say> : face.isError ? <Say tone="bad">{isForbidden(face.error) ? `You may not change ${lemma.name}.` : problemOf(face.error, "The face could not be saved. Try again.")}</Say> : null}
                </View>
            </View>
            <Field label="Name" value={name} onChange={setName} maxLength={255} autoCapitalize="words" />
            <Field label="What it is for" value={job} onChange={setJob} multiline placeholder="Watches the inbox and answers what it can" />
            {save.isError ? (
                <Say tone="bad">{isForbidden(save.error) ? `You may not change ${lemma.name}.` : "That could not be saved. Try again."}</Say>
            ) : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow onPress={() => router.back()} />
                <Button title={save.isPending ? "Saving…" : "Save"} grow busy={save.isPending} disabled={!name.trim() || !changed} onPress={() => save.mutate({ name, description: job }, { onSuccess: () => router.back() })} />
            </View>
            <FaceSheet
                open={faces}
                id={lemma.id}
                icon={lemma.iconUrl}
                onClose={() => setFaces(false)}
                onPick={(next) => { setFaces(false); face.mutate(next); }}
            />
        </SettingsScreen>
    );
}

const CHARACTERS = Object.keys(CAST) as CharacterName[];

/** A picture from the phone, one of the cast, or the face its id draws. */
function FaceSheet({ open, id, icon, onClose, onPick }: {
    open: boolean;
    id: string;
    icon: string | null;
    onClose: () => void;
    onPick: (face: { picture: PickedFile } | { icon: string | null }) => void;
}) {
    const [problem, setProblem] = useState<string | null>(null);
    const own = characterForSeed(id);
    return (
        <Sheet open={open} onClose={onClose} title="Its face" tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 14 }}>
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button
                        title="Choose a photo"
                        kind="outline"
                        grow
                        onPress={() => {
                            setProblem(null);
                            void pickSquarePhoto().then((picture) => { if (picture) onPick({ picture }); }, (error: unknown) => setProblem(problemOf(error, "The photos could not be opened.")));
                        }}
                    />
                    <Button title="Its own face" kind="outline" grow disabled={!icon} onPress={() => onPick({ icon: null })} />
                </View>
                {problem ? <Say tone="bad">{problem}</Say> : null}
                <Text size={12.5} tone={color.ink3}>Or one of these</Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 10 }}>
                    {CHARACTERS.map((name) => {
                        const variant = variantFor(id, name, characterForSeed);
                        if (variant === null) return null;
                        const value = identityIcon(variant);
                        const held = icon === value || (!icon && name === own);
                        return (
                            <Press
                                key={name}
                                role="radio"
                                label={`The ${name} face`}
                                accessibilityState={{ selected: held }}
                                onPress={() => onPick({ icon: value })}
                                style={{ width: 64, height: 64, borderRadius: 18, borderWidth: held ? 2 : 1, borderColor: held ? color.ink : color.line2, alignItems: "center", justifyContent: "center" }}
                            >
                                <Image source={CAST[name]} style={{ width: 52, height: 52 }} contentFit="contain" />
                            </Press>
                        );
                    })}
                </View>
            </ScrollView>
        </Sheet>
    );
}

/** The last thing on a Lemma's Settings: the way to delete it, for the
 *  people the server will let. Everybody else sees nothing, as on the web:
 *  a section that only says "you can't" is about somebody else's
 *  permissions. Typing the name is the confirmation. */
export function DeleteLemma({ podId, name }: { podId: string; name: string }) {
    const may = useMayI(podId, DELETE_POD);
    const [asking, setAsking] = useState(false);
    const router = useRouter();
    const remove = useDeleteLemma(podId);
    if (!may.allowed) return null;
    return (
        <View style={{ gap: 8, paddingTop: 6 }}>
            <Text size={12.5} tone={color.ink3}>Delete</Text>
            <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>Everything in {name} goes with it, for everyone here.</Text>
            <View style={{ flexDirection: "row" }}>
                <Button title={`Delete ${name}`} kind="outline" onPress={() => setAsking(true)} />
            </View>
            <Confirm
                open={asking}
                title={`Delete ${name}?`}
                body={`For everyone here, as soon as you delete ${name}: its chats, pages, tables, files and apps can no longer be opened. Its standing work stops. It leaves Slack, WhatsApp and email, and stops answering there. Its email address and phone number are released. There is no undo.`}
                confirm={remove.isPending ? "Deleting…" : `Delete ${name}`}
                typeToConfirm={name}
                busy={remove.isPending}
                error={remove.isError ? (isForbidden(remove.error) ? `You may not delete ${name}.` : `${name} could not be deleted. Try again.`) : null}
                onClose={() => { if (!remove.isPending) setAsking(false); }}
                onConfirm={() => remove.mutate(undefined, { onSuccess: () => { setAsking(false); router.replace(routes.lemmas as never); } })}
            />
        </View>
    );
}
