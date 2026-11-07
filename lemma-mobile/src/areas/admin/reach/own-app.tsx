import { useEffect, useState, type ReactNode } from "react";
import { View } from "react-native";
import * as Crypto from "expo-crypto";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { copyText } from "../../../thread/clipboard";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { openOutside } from "../connect/round-trip";
import { adminKey } from "../hooks";
import { ChoiceRow, Field, Say } from "../ui";
import { useReachSource, useSlackAppWorkspaces, useSlackManaged, useSlackManifest } from "./hooks";
import { SLACK_APPS, connectProblem, slackManifestLink, workspaceName } from "./places";
import { SignInStep, Waiting, useSignIn } from "./sign-in";
import type { SlackAppWorkspace } from "./types";
import type { Connectable } from "@web/data/connectable";

/** Slack (the web's `shell/own-bot.tsx` and `SlackManagerStep`).
 *
 *  One Slack app is one bot user, and a connected account answers as one
 *  Lemma in an organization, so each Lemma on Slack gets an app of its own,
 *  named after it. Lemma makes that app when the organization has let it
 *  (a configuration token saved once per workspace); otherwise the person
 *  makes it from a manifest the server writes, pastes its credentials, and
 *  authorises it. Lemma's own app comes first in a workspace: it looks after
 *  the workspace's Lemmas and answers nobody itself. */

interface Props {
    podId: string;
    orgId: string | null;
    space: string;
    entry: Connectable;
    onDone: () => void;
}

/** Lemma's own app in the workspace first, then this Lemma's own. */
export function SlackManagerStep({ podId, orgId, space, entry, onReady }: Omit<Props, "onDone"> & { onReady: () => void }) {
    const source = useReachSource();
    const managed = useSlackManaged(podId);
    const has = Boolean(managed.data?.length);
    useEffect(() => { if (has) onReady(); }, [has, onReady]);
    const flow = useSignIn({
        orgId,
        connectorId: entry.connectorId,
        /* Lemma's own app, never a Lemma's own Slack app: both are installs
           of the same connector, and only Lemma's may look after a workspace. */
        configSource: "SYSTEM_DEFAULT",
        bind: (accountId) => source.bindSlackManager(podId, accountId),
        onDone: onReady,
    });
    if (managed.isPending || has) return <Waiting>Checking your Slack workspace…</Waiting>;
    return (
        <View style={{ gap: 12 }}>
            <Heading>Add Lemma to your Slack workspace</Heading>
            <Body>{`Lemma's app is where your workspace sees its Lemmas, adds them and gives them places to answer. It answers nobody itself. Then ${space} gets a Slack app of its own.`}</Body>
            {managed.isError ? <Say tone="bad">{connectProblem(managed.error, "Your Slack workspaces could not be read.")}</Say> : null}
            <SignInStep name="Slack" label="Add Lemma to Slack" flow={flow} />
            <Quiet label={`Skip this, and give ${space} its app now`} onPress={onReady} />
        </View>
    );
}

/** Where the app comes from: Lemma, when the organization has let it; by
 *  hand otherwise. */
export function OwnApp({ podId, orgId, space, entry, onDone }: Props) {
    const [byHand, setByHand] = useState(false);
    const workspaces = useSlackAppWorkspaces(podId);
    const hand = <Quiet label="Make the app by hand instead" onPress={() => setByHand(true)} />;

    if (byHand || workspaces.isError) return <ByHand podId={podId} orgId={orgId} space={space} entry={entry} onDone={onDone} />;
    if (workspaces.isPending) return <Waiting>Checking what Lemma can do here…</Waiting>;
    /* Saving a token and making an app are an organization owner's or
       editor's: offering the form to anybody else only ends in a refusal. */
    if (!workspaces.data.canManage) {
        return (
            <View style={{ gap: 12 }}>
                <Say>{`An owner or editor of your organization can let Lemma make Slack apps, so ${space}'s is one tap. Until then, make the app by hand.`}</Say>
                <ByHand podId={podId} orgId={orgId} space={space} entry={entry} onDone={onDone} />
            </View>
        );
    }
    const usable = workspaces.data.workspaces.filter((one) => !one.needsNewToken);
    if (!usable.length) {
        return (
            <View style={{ gap: 12 }}>
                <LetLemmaMakeApps podId={podId} space={space} expired={workspaces.data.workspaces.find((one) => one.needsNewToken) ?? null} onSaved={() => void workspaces.refetch()} />
                {hand}
            </View>
        );
    }
    return (
        <View style={{ gap: 12 }}>
            <MadeByLemma podId={podId} orgId={orgId} space={space} entry={entry} workspaces={usable} onDone={onDone} />
            {hand}
        </View>
    );
}

/** Once per workspace: the refresh token of a configuration token, which
 *  lets Lemma make apps there. Slack makes every app as the person whose
 *  token it is, so they are among its owners. */
function LetLemmaMakeApps({ podId, space, expired, onSaved }: { podId: string; space: string; expired: SlackAppWorkspace | null; onSaved: () => void }) {
    const source = useReachSource();
    const [token, setToken] = useState("");
    const save = useMutation({
        mutationFn: () => source.saveSlackToken(podId, token),
        onSuccess: () => { setToken(""); onSaved(); },
    });
    return (
        <View style={{ gap: 12 }}>
            {expired ? <Say tone="bad">{`Slack no longer accepts the token saved for ${workspaceName(expired)}. Make a new one and paste its refresh token.`}</Say> : null}
            <Body>{`Let Lemma make Slack apps in your workspace, and ${space}'s is one tap. On Slack's apps page, under Your App Configuration Tokens, choose Generate Token, pick the workspace, and paste the refresh token here. Once per workspace.`}</Body>
            <Field label="Refresh token" value={token} onChange={setToken} placeholder="xoxe-1-…" secure editable={!save.isPending} />
            {save.isError ? <Say tone="bad">{connectProblem(save.error, "Slack did not take that token.")}</Say> : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Open Slack apps" kind="outline" grow onPress={() => void openOutside(SLACK_APPS)} />
                <Button title={save.isPending ? "Checking with Slack…" : "Save"} grow busy={save.isPending} disabled={!token.trim()} onPress={() => save.mutate()} />
            </View>
        </View>
    );
}

/** Lemma makes the app and keeps its credentials; the person authorises it. */
function MadeByLemma({ podId, orgId, space, entry, workspaces, onDone }: Props & { workspaces: SlackAppWorkspace[] }) {
    const source = useReachSource();
    const cache = useQueryClient();
    const key = useCacheKey();
    const [teamId, setTeamId] = useState(workspaces[0].teamId);
    /* One key for this screen: a second press, or a retry after the sign-in
       failed to start, answers with the app the first one made. */
    const [attempt] = useState(() => Crypto.randomUUID());
    const [made, setMade] = useState<{ appId: string; authConfigId: string } | null>(null);
    const chosen = workspaces.find((one) => one.teamId === teamId) ?? workspaces[0];
    const create = useMutation({
        mutationFn: () => source.createSlackApp(podId, teamId, `${attempt}:${teamId}`),
        onSuccess: setMade,
        /* A token Slack refused marks its workspace: read the list again, so
           the screen asks for a new one instead of offering this again. */
        onError: () => void cache.invalidateQueries({ queryKey: adminKey(key, "reach", podId, "slack-apps") }),
    });
    return (
        <View>
            <Step n={1} title={`Make ${space}'s app in ${workspaceName(chosen)}`} done={Boolean(made)} now={!made}>
                <Body>{`Lemma makes it from the manifest, names it ${space}, and keeps its credentials.`}</Body>
                {!made ? (
                    <View style={{ gap: 8 }}>
                        {workspaces.length > 1 ? (
                            <ChoiceRow label="Workspace" choices={workspaces.map((one) => ({ value: one.teamId, label: workspaceName(one) }))} value={teamId} onPick={setTeamId} last />
                        ) : null}
                        <View style={{ flexDirection: "row" }}>
                            <Button title={create.isPending ? "Making it…" : "Make the app"} busy={create.isPending} onPress={() => create.mutate()} />
                        </View>
                        {create.isError ? <Say tone="bad">{connectProblem(create.error, "Slack did not make the app.")}</Say> : null}
                    </View>
                ) : null}
            </Step>
            <Step n={2} title="Authorise it" now={Boolean(made)} last>
                <Body>{`The one step only you can take, and the one that hands ${space} the app.`}</Body>
                {made ? (
                    <>
                        <Authorise podId={podId} orgId={orgId} entry={entry} authConfigId={made.authConfigId} onDone={onDone} />
                        <Say>Slack takes the app's picture only on its own page.</Say>
                        <Quiet label={`Add ${space}'s picture in Slack`} onPress={() => void openOutside(`${SLACK_APPS}/${made.appId}/general`)} />
                    </>
                ) : null}
            </Step>
        </View>
    );
}

/** Sign in to Slack against the app just made, then put it to use. */
function Authorise({ podId, orgId, entry, authConfigId, onDone }: { podId: string; orgId: string | null; entry: Connectable; authConfigId: string; onDone: () => void }) {
    const source = useReachSource();
    const flow = useSignIn({
        orgId,
        connectorId: entry.connectorId,
        authConfigId,
        bind: (accountId) => source.connectOwnAccount(podId, entry.platform, accountId),
        onDone,
    });
    return <SignInStep name="Slack" label="Authorise in Slack" flow={flow} />;
}

/** The app made by hand: the manifest, the app made in Slack from it, its
 *  credentials pasted here, and the sign-in. */
function ByHand({ podId, orgId, space, entry, onDone }: Props) {
    const source = useReachSource();
    const manifest = useSlackManifest(space);
    const [step, setStep] = useState(0);
    const [clientId, setClientId] = useState("");
    const [secret, setSecret] = useState("");
    const [signing, setSigning] = useState("");
    const [installId, setInstallId] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const text = manifest.data ? JSON.stringify(manifest.data, null, 2) : "";
    const register = useMutation({
        mutationFn: async () => {
            if (!orgId) throw new Error("No organization is open.");
            /* `ORG_CUSTOM` makes the sign-in run against this app, not
               Lemma's own. Made once: a retry reuses it. */
            const made = installId ?? await source.addOwnApp(orgId, entry.connectorId, `${space} Slack app`, {
                client_id: clientId.trim(),
                client_secret: secret.trim(),
                signing_secret: signing.trim(),
            }, entry.kind);
            setInstallId(made);
            setSecret("");
            setSigning("");
            return made;
        },
        onSuccess: () => setStep(3),
    });

    return (
        <View>
            <Step n={1} title="Copy the manifest" done={step > 0} now={step === 0}>
                <Body>{`It already carries this server's addresses and the permissions Lemma expects, and it names the app ${space}.`}</Body>
                {step === 0 ? (
                    <View style={{ gap: 8 }}>
                        {manifest.isPending ? <Waiting>Fetching it…</Waiting> : null}
                        {manifest.isError ? <Say tone="bad">The manifest could not be read.</Say> : null}
                        {text ? (
                            <View style={{ flexDirection: "row", gap: 10 }}>
                                <Button title={copied ? "Copied" : "Copy the manifest"} kind="outline" grow onPress={() => { copyText(text); setCopied(true); setTimeout(() => setCopied(false), 1600); }} />
                                <Button title="Next" grow onPress={() => setStep(1)} />
                            </View>
                        ) : null}
                    </View>
                ) : null}
            </Step>
            <Step n={2} title="Make the app in Slack" done={step > 1} now={step === 1}>
                <Body>On Slack's apps page choose Create New App, then From a manifest, pick your workspace, and paste it. Then install it to the workspace.</Body>
                {step === 1 && manifest.data ? (
                    <View style={{ gap: 8 }}>
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Open it in Slack" grow onPress={() => void openOutside(slackManifestLink(manifest.data))} />
                            <Button title="Open Slack apps" kind="outline" grow onPress={() => void openOutside(SLACK_APPS)} />
                        </View>
                        <Say>Open it in Slack starts the new app with this manifest already in it.</Say>
                        <Quiet label="Done that" onPress={() => setStep(2)} />
                    </View>
                ) : null}
            </Step>
            <Step n={3} title="Paste the app's credentials" done={step > 2} now={step === 2}>
                <Body>From the app's Basic Information page. They are your organization's own, so the sign-in runs against your app rather than Lemma's.</Body>
                {step === 2 ? (
                    <View style={{ gap: 12 }}>
                        {!installId ? (
                            <>
                                <Field label="Client ID" value={clientId} onChange={setClientId} autoCapitalize="none" editable={!register.isPending} />
                                <Field label="Client secret" value={secret} onChange={setSecret} secure editable={!register.isPending} />
                                <Field label="Signing secret" value={signing} onChange={setSigning} secure editable={!register.isPending} />
                            </>
                        ) : null}
                        {register.isError ? <Say tone="bad">{connectProblem(register.error, "Those credentials were not accepted.")}</Say> : null}
                        <View style={{ flexDirection: "row" }}>
                            <Button
                                title={register.isPending ? "Registering…" : "Register the app"}
                                busy={register.isPending}
                                disabled={!installId && (!clientId.trim() || !secret.trim() || !signing.trim())}
                                onPress={() => register.mutate()}
                            />
                        </View>
                    </View>
                ) : null}
            </Step>
            <Step n={4} title="Authorise it" now={step === 3} last>
                <Body>{`The last step, and the one that hands ${space} the app.`}</Body>
                {step === 3 && installId ? <Authorise podId={podId} orgId={orgId} entry={entry} authConfigId={installId} onDone={onDone} /> : null}
            </Step>
        </View>
    );
}

/* ── pieces ─────────────────────────────────────────────────────────── */

/** One step of a few: its number (a tick once done), its title, and its
 *  body only while it is the one being done. Rules between, no boxes. */
function Step({ n, title, done = false, now = false, last = false, children }: { n: number; title: string; done?: boolean; now?: boolean; last?: boolean; children: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", gap: 12, paddingVertical: 12, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line, opacity: done || now ? 1 : 0.55 }}>
            <View style={{ width: 22, alignItems: "center", paddingTop: 1 }}>
                {done ? <Icon name="check" size={16} width={2} /> : <Num size={15} tone={now ? color.ink : color.ink3}>{n}</Num>}
            </View>
            <View style={{ flex: 1, gap: 8 }}>
                <Text size={16} weight={now ? "medium" : "regular"}>{title}</Text>
                {now ? children : null}
            </View>
        </View>
    );
}

export function Heading({ children }: { children: string }) {
    return <Text size={18} weight="medium" accessibilityRole="header">{children}</Text>;
}

export function Body({ children }: { children: string }) {
    return <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{children}</Text>;
}

export function Quiet({ label, onPress }: { label: string; onPress: () => void }) {
    return (
        <Press label={label} onPress={onPress} style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}>
            <Text size={15} weight="medium">{label}</Text>
        </Press>
    );
}

