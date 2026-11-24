import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import type { Org } from "../../../data/types";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useMyOrgRole } from "../access/hooks";
import { ChoiceRow, Field, Group, Reading, Say, SettingsScreen, ToggleRow, isForbidden, problemOf } from "../ui";
import { canManage } from "@web/org/membership";
import { useAddKey, useSetOrgDefault } from "./hooks";
import { PRESETS } from "./model";
import { WithOrg } from "./parts";
import { asksAboutImages, chosenVisionModels, modelNames, visionCandidates } from "@web/org/provider-draft";
import type { Runtime } from "@web/data/runtimes";

/** Connect a provider's key for the whole organization (the web's "Connect a
 *  key"), as a screen of its own so the keyboard never covers a field. The
 *  key is a secret field: drawn as dots, never offered to autofill, sent once
 *  and dropped from the screen when it is saved. A saved key is tested from
 *  its row under Models. */
export function AddKeyScreen() {
    return (
        <WithOrg title="Connect a key" back="Models">
            {(org) => <AddKey org={org} />}
        </WithOrg>
    );
}

function AddKey({ org }: { org: Org }) {
    const router = useRouter();
    const { role, answered } = useMyOrgRole(org.id);
    const add = useAddKey(org.id);
    const setDefault = useSetOrgDefault(org.id);
    const [presetId, setPresetId] = useState(PRESETS[0].id);
    const preset = PRESETS.find((one) => one.id === presetId) ?? PRESETS[0];
    const [name, setName] = useState(PRESETS[0].name);
    const [baseUrl, setBaseUrl] = useState(PRESETS[0].baseUrl);
    const [apiKey, setApiKey] = useState("");
    const [models, setModels] = useState("");
    const [vision, setVision] = useState<string[]>([]);
    const [problem, setProblem] = useState<string | null>(null);
    const [offer, setOffer] = useState<Runtime | null>(null);
    const note = `Billed to the provider account the key belongs to, and shared with everyone in ${org.name}.`;

    if (!answered) {
        return <SettingsScreen title="Connect a key" note={note} back="Models"><Reading /></SettingsScreen>;
    }
    if (role !== null && !canManage(role)) {
        return (
            <SettingsScreen title="Connect a key" back="Models">
                <Say>Only owners and editors of {org.name} connect keys. Ask one of them.</Say>
            </SettingsScreen>
        );
    }

    /* The first thing able to answer at all, and nobody has chosen a default:
       asked once, right after it lands. */
    if (offer) {
        return (
            <SettingsScreen title={`Use ${offer.name} as the default?`} note="Anything that names no model will run on it. You can change this under Models at any time." back="Models">
                {setDefault.isError ? (
                    <Say tone="bad">{isForbidden(setDefault.error) ? `Only owners and editors of ${org.name} choose its default.` : problemOf(setDefault.error, "That could not be made the default. Try again.")}</Say>
                ) : null}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Not now" kind="outline" grow onPress={() => router.back()} />
                    <Button title={setDefault.isPending ? "Saving…" : "Use it"} grow busy={setDefault.isPending} onPress={() => setDefault.mutate({ runtimeId: offer.id, model: "" }, { onSuccess: () => router.back() })} />
                </View>
            </SettingsScreen>
        );
    }

    const typed = modelNames(models);
    const candidates = visionCandidates(typed, []);
    const ready = Boolean(name.trim() && apiKey.trim() && (preset.protocol === "anthropic" || baseUrl.trim()));
    const pick = (id: string) => {
        const next = PRESETS.find((one) => one.id === id) ?? PRESETS[0];
        setPresetId(next.id);
        setName(next.name);
        setBaseUrl(next.baseUrl);
        setProblem(null);
    };
    const connect = () => {
        setProblem(null);
        add.mutate({
            protocol: preset.protocol,
            name: name.trim(),
            baseUrl: baseUrl.trim(),
            apiKey: apiKey.trim(),
            models: typed,
            visionModels: chosenVisionModels(preset.protocol, vision, candidates),
        }, {
            onSuccess: (first) => {
                setApiKey("");
                if (first) setOffer(first);
                else router.back();
            },
            onError: (error) => setProblem(isForbidden(error) ? `Only owners and editors of ${org.name} connect keys.` : problemOf(error, "That key could not be saved. Check it and try again.")),
        });
    };

    return (
        <SettingsScreen title="Connect a key" note={note} back="Models">
            <Group>
                <ChoiceRow label="Provider" title="Provider" choices={PRESETS.map((one) => ({ value: one.id, label: one.name }))} value={preset.id} onPick={pick} last />
            </Group>
            <Field label="Name" value={name} onChange={setName} autoCapitalize="words" maxLength={255} />
            <Field
                label="API address"
                value={baseUrl}
                onChange={setBaseUrl}
                placeholder="https://…"
                keyboardType="url"
                hint={preset.protocol === "anthropic" ? "Leave it as it is unless the key is for another Anthropic-style route." : null}
            />
            <Field
                label="API key"
                value={apiKey}
                onChange={(next) => { setApiKey(next); setProblem(null); }}
                secure
                hint="Sent to the server once. Nobody can read it back, here or on the web."
            />
            <Field
                label="Models, if you want to name them"
                value={models}
                onChange={(next) => { setModels(next); setVision((was) => was.filter((one) => modelNames(next).includes(one))); }}
                placeholder="gpt-5, o3-mini"
                keyboardType="url"
                hint="Comma separated. Left empty, the provider's own list is used."
            />
            {asksAboutImages(preset.protocol) && candidates.length > 0 ? (
                <Group label="Reads images" foot="Ticked models are handed pictures and PDF pages directly. The rest are never sent one.">
                    {candidates.map((model, index) => (
                        <ToggleRow
                            key={model}
                            label={model}
                            value={vision.includes(model)}
                            onChange={(on) => setVision((was) => (on ? [...was, model] : was.filter((one) => one !== model)))}
                            last={index === candidates.length - 1}
                        />
                    ))}
                </Group>
            ) : null}
            {problem ? <Say tone="bad">{problem}</Say> : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow onPress={() => router.back()} />
                <Button title={add.isPending ? "Saving…" : "Connect"} grow busy={add.isPending} disabled={!ready} onPress={connect} />
            </View>
            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>Once it is saved, test it from its row under Models.</Text>
        </SettingsScreen>
    );
}
